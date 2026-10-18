#!/usr/bin/env python3
"""
===============================================================================
DUSKY HYPRLOCK: FINGERPRINT AUTHENTICATION SETUP & MANAGEMENT ENGINE
===============================================================================
Provides end-to-end hardware detection, driver verification, PAM stack integration,
clamshell (lid-closed) gating, systemd suspend-recovery, enrollment, verification,
and Hyprlock configuration for Arch Linux / Hyprland environments.

Targets CPython 3.15+ (CPython 3.15.0rc3 baseline).
Zero hardcoded usernames, home directories, or machine-specific device identifiers.
===============================================================================
"""

lazy import argparse
lazy import dataclasses
lazy import json
lazy import os
lazy import pwd
lazy import re
lazy import shutil
lazy import subprocess
lazy import sys
lazy import time
lazy from pathlib import Path
lazy from typing import Any, Final

# Multi-purpose vendors (Elan, STMicro) are excluded from raw vendor match to
# avoid false positives on touchscreen laptops; they match on product string.
KNOWN_FINGERPRINT_VENDORS: Final = frozenset({
    "27c6",  # Goodix
    "138a",  # Validity Sensors / Synaptics
    "06cb",  # Synaptics
    "08ff",  # AuthenTec
    "1c7a",  # LighTuning / EgisTec
    "147e",  # Upek
})

# Default system paths (overridable via environment variables for hermetic testing)
ENV_USB_DEVICES_PATH: Final = "DUSKY_USB_DEVICES_PATH"
ENV_ACPI_LID_PATH: Final = "DUSKY_ACPI_LID_PATH"
ENV_INPUT_CLASS_PATH: Final = "DUSKY_INPUT_CLASS_PATH"
ENV_PAM_DIR: Final = "DUSKY_PAM_DIR"
ENV_HYPR_DIR: Final = "DUSKY_HYPR_DIR"
ENV_SYSTEMD_SLEEP_DIR: Final = "DUSKY_SYSTEMD_SLEEP_DIR"
ENV_SYSTEMD_SERVICE_DIR: Final = "DUSKY_SYSTEMD_SERVICE_DIR"
ENV_FPRINT_DIR: Final = "DUSKY_FPRINT_DIR"
ENV_GATE_BIN: Final = "DUSKY_GATE_BIN"
ENV_MOCK_PACKAGES: Final = "DUSKY_MOCK_PACKAGES"
ENV_SIMULATE_SENSOR: Final = "DUSKY_SIMULATE_SENSOR"

DEFAULT_USB_DEVICES_PATH: Final = Path("/sys/bus/usb/devices")
DEFAULT_ACPI_LID_PATH: Final = Path("/proc/acpi/button/lid")
DEFAULT_INPUT_CLASS_PATH: Final = Path("/sys/class/input")
DEFAULT_PAM_DIR: Final = Path("/etc/pam.d")
DEFAULT_SYSTEMD_SLEEP_DIR: Final = Path("/usr/lib/systemd/system-sleep")
DEFAULT_SYSTEMD_SERVICE_DIR: Final = Path("/etc/systemd/system")
DEFAULT_FPRINT_DIR: Final = Path("/var/lib/fprint")

CLAMSHELL_GATE_BIN: Final = "/usr/local/bin/dusky-hw-laptop-closed"

def get_clamshell_gate_bin() -> str:
    """Returns the gate helper path, overridable by DUSKY_GATE_BIN for hermetic tests."""
    return os.environ.get(ENV_GATE_BIN, CLAMSHELL_GATE_BIN)

def get_pam_gate_rule() -> str:
    """Returns the PAM gate rule referencing the current gate binary."""
    return f"auth      [success=1 default=ignore] pam_exec.so quiet {get_clamshell_gate_bin()}"

def ensure_sudo() -> bool:
    """
    Verifies or refreshes sudo credentials upfront.
    If running as root or DUSKY_NO_SUDO=1, succeeds immediately.
    Checks if sudo is already validated (sudo -n true).
    Otherwise runs 'sudo -v' (or prompts) upfront.
    """
    if os.geteuid() == 0 or os.environ.get("DUSKY_NO_SUDO") == "1":
        return True

    # Fast path: already validated in credential cache
    try:
        res = subprocess.run(["sudo", "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if res.returncode == 0:
            return True
    except OSError:
        return False

    if not sys.stdin.isatty():
        try:
            res = subprocess.run(["sudo", "-S", "-v"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return res.returncode == 0
        except OSError:
            return False

    try:
        print("\033[1;34m[::]\033[0m Sudo authorization required. Validating credentials...")
        res = subprocess.run(["sudo", "-v"], check=False)
        return res.returncode == 0
    except OSError:
        return False

PAM_FPRINT_RULE: Final = "auth      sufficient pam_fprintd.so"


# =============================================================================
# 1. DATA STRUCTURES & VALUE OBJECTS
# =============================================================================

@dataclasses.dataclass(frozen=True)
class FingerprintDevice:
    """Represents a discovered fingerprint reader hardware device."""
    vendor_id: str
    product_id: str
    product_name: str
    sysfs_path: str
    matched_by: str  # "product" or "vendor"

    def to_dict(self) -> dict[str, str]:
        return {
            "vendor_id": self.vendor_id,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "sysfs_path": self.sysfs_path,
            "matched_by": self.matched_by,
        }


@dataclasses.dataclass(frozen=True)
class ClamshellStatus:
    """Represents the evaluated lid status of a laptop."""
    is_closed: bool
    lid_detected: bool
    method_used: str  # "acpi", "input_switch", "logind", or "none"
    detail: str


@dataclasses.dataclass(frozen=True)
class SystemStatus:
    """Comprehensive system status report."""
    hardware_present: bool
    devices: list[dict[str, str]]
    packages_installed: dict[str, bool]
    fprintd_service_active: bool
    sudo_pam_configured: bool
    polkit_pam_configured: bool
    clamshell_gate_installed: bool
    resume_hook_installed: bool
    stop_timeout_installed: bool
    hyprlock_configured: bool
    enrolled_fingers: list[str]
    probe_classification: str
    lid_status: dict[str, Any]


# =============================================================================
# 2. IDENTITY RESOLUTION (SAFE & SPOOF-PROOF)
# =============================================================================

class IdentityResolver:
    """
    Safely resolves the target user account for fingerprint operations.
    Guards against environment spoofing, path traversals, and numeric UID collisions.
    """

    SAFE_USERNAME_RE: Final = re.compile(r"^[a-zA-Z0-9_.][a-zA-Z0-9_.-]*$")

    @classmethod
    def resolve_target_user(cls) -> str:
        """
        Determines the invoking username.
        If running as root under sudo, resolves the numeric SUDO_UID via getpwuid
        (strictly avoiding username collision where an account is named after a UID).
        """
        euid = os.geteuid()
        sudo_uid_env = os.environ.get("SUDO_UID")

        if euid == 0 and sudo_uid_env is not None:
            if not sudo_uid_env.isdigit():
                raise ValueError(f"Invalid non-numeric SUDO_UID: {sudo_uid_env!r}")
            uid_num = int(sudo_uid_env)
            try:
                username = pwd.getpwuid(uid_num).pw_name
            except KeyError as err:
                raise RuntimeError(f"Cannot resolve invoking user for SUDO_UID={uid_num}") from err
        else:
            try:
                username = pwd.getpwuid(euid).pw_name
            except KeyError as err:
                raise RuntimeError(f"Cannot resolve username for EUID={euid}") from err

        return cls.validate_username(username)

    @classmethod
    def validate_username(cls, username: str) -> str:
        """Validates that a username is safe for filesystem and PAM operations."""
        if not username:
            raise ValueError("Username cannot be empty")
        if username in {".", ".."}:
            raise ValueError(f"Unsafe dot username: {username!r}")
        if "/" in username or "\\" in username:
            raise ValueError(f"Username cannot contain path separators: {username!r}")
        if not cls.SAFE_USERNAME_RE.fullmatch(username):
            raise ValueError(f"Username contains disallowed characters: {username!r}")
        return username

    @classmethod
    def resolve_user_home(cls, username: str | None = None) -> Path:
        """
        Resolves the true home directory for a target user.
        Guards against Path.home() pointing to /root when executing under sudo.
        """
        user = cls.validate_username(username) if username else cls.resolve_target_user()
        try:
            return Path(pwd.getpwnam(user).pw_dir)
        except KeyError as err:
            raise RuntimeError(f"Cannot resolve home directory for user {user!r}") from err


# =============================================================================
# 3. HARDWARE DETECTION (SYSFS SCANNER)
# =============================================================================

class HardwareDetector:
    """
    Direct sysfs scanner that identifies fingerprint readers before fprintd
    or usbutils are installed. Matches either vendor-agnostic product descriptors
    or known vendor IDs without kernel driver interface bindings.
    """

    def __init__(self, sysfs_usb_root: Path | None = None, simulate: bool = False) -> None:
        self.usb_root = sysfs_usb_root or Path(
            os.environ.get(ENV_USB_DEVICES_PATH, str(DEFAULT_USB_DEVICES_PATH))
        )
        self.simulate = simulate or (os.environ.get(ENV_SIMULATE_SENSOR) == "1")

    def _has_kernel_driver(self, dev_dir: Path) -> bool:
        """
        Checks if any interface under the USB device directory is bound to a
        kernel driver. 'usbfs' is excluded because libusb claims interfaces
        through usbfs from userspace (which is expected for libfprint).
        """
        try:
            for entry in dev_dir.iterdir():
                if ":" not in entry.name:
                    continue
                driver_link = entry / "driver"
                if driver_link.exists():
                    try:
                        resolved = driver_link.resolve()
                        if resolved.name != "usbfs":
                            return True
                    except OSError:
                        return True
        except OSError:
            return False
        return False

    def detect_readers(self) -> list[FingerprintDevice]:
        """Scans the USB tree and returns all detected fingerprint readers."""
        if self.simulate:
            return [
                FingerprintDevice(
                    vendor_id="27c6",
                    product_id="5385",
                    product_name="Simulated Goodix Fingerprint USB Device",
                    sysfs_path="/sys/bus/usb/devices/simulated-1",
                    matched_by="simulated",
                )
            ]

        if not self.usb_root.is_dir():
            return []

        detected: list[FingerprintDevice] = []

        try:
            entries = sorted(self.usb_root.iterdir(), key=lambda p: p.name)
        except OSError:
            return []

        for dev_dir in entries:
            if not dev_dir.is_dir():
                continue
            product_file = dev_dir / "product"
            vendor_file = dev_dir / "idVendor"
            product_id_file = dev_dir / "idProduct"

            product = ""
            vendor = ""
            product_id = ""

            if product_file.is_file():
                try:
                    product = product_file.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    product = ""

            if vendor_file.is_file():
                try:
                    vendor = vendor_file.read_text(encoding="utf-8", errors="replace").strip().lower()
                except OSError:
                    vendor = ""

            if product_id_file.is_file():
                try:
                    product_id = product_id_file.read_text(encoding="utf-8", errors="replace").strip().lower()
                except OSError:
                    product_id = ""

            # 1. Product string check (trusted outright regardless of bound drivers)
            prod_lower = product.lower()
            if prod_lower:
                is_prod_match = (
                    "fingerprint" in prod_lower
                    or "biometric" in prod_lower
                    or "elan:arm-m4" in prod_lower
                    or prod_lower.startswith("fpc ")
                )
                if is_prod_match:
                    detected.append(
                        FingerprintDevice(
                            vendor_id=vendor,
                            product_id=product_id,
                            product_name=product,
                            sysfs_path=str(dev_dir),
                            matched_by="product",
                        )
                    )
                    continue

            # 2. Known vendor ID check (only trusted if no kernel driver is bound)
            if vendor and vendor in KNOWN_FINGERPRINT_VENDORS:
                if not self._has_kernel_driver(dev_dir):
                    detected.append(
                        FingerprintDevice(
                            vendor_id=vendor,
                            product_id=product_id,
                            product_name=product or f"USB Device [{vendor}:{product_id}]",
                            sysfs_path=str(dev_dir),
                            matched_by="vendor",
                        )
                    )

        return detected

    def has_reader(self) -> bool:
        """Returns True if at least one fingerprint reader is present."""
        return len(self.detect_readers()) > 0


# =============================================================================
# 4. CLAMSHELL (LID-CLOSED) GATE DETECTION
# =============================================================================

class ClamshellGate:
    """
    Evaluates laptop lid state via ACPI, Linux input switch bitmaps, or systemd-logind.
    When the lid is closed, the reader is physically inaccessible; gating skips
    pam_fprintd so PAM falls straight to the password prompt without a 30s timeout.
    """

    def __init__(
        self,
        acpi_lid_path: Path | None = None,
        input_class_path: Path | None = None,
    ) -> None:
        self.acpi_path = acpi_lid_path or Path(
            os.environ.get(ENV_ACPI_LID_PATH, str(DEFAULT_ACPI_LID_PATH))
        )
        self.input_path = input_class_path or Path(
            os.environ.get(ENV_INPUT_CLASS_PATH, str(DEFAULT_INPUT_CLASS_PATH))
        )

    def _check_acpi(self) -> ClamshellStatus | None:
        """Checks /proc/acpi/button/lid/*/state."""
        if not self.acpi_path.is_dir():
            return None

        try:
            states = sorted(self.acpi_path.glob("*/state"), key=lambda p: p.name)
        except OSError:
            return None

        if not states:
            return None

        found_open = False
        open_detail = ""

        for state_file in states:
            try:
                content = state_file.read_text(encoding="utf-8", errors="replace").strip().lower()
                if "closed" in content:
                    return ClamshellStatus(
                        is_closed=True,
                        lid_detected=True,
                        method_used="acpi",
                        detail=f"ACPI state in {state_file.name} reported closed",
                    )
                if "open" in content:
                    found_open = True
                    open_detail = f"ACPI state in {state_file.name} reported open"
            except OSError:
                continue

        if found_open:
            return ClamshellStatus(
                is_closed=False,
                lid_detected=True,
                method_used="acpi",
                detail=open_detail,
            )

        return None

    def _has_input_lid_switch(self) -> bool:
        """Checks /sys/class/input/*/capabilities/sw for SW_LID (bit 0 of last word)."""
        if not self.input_path.is_dir():
            return False

        try:
            switches = list(self.input_path.glob("*/capabilities/sw"))
        except OSError:
            return False

        for sw_file in switches:
            try:
                words = sw_file.read_text(encoding="utf-8", errors="replace").split()
                if not words:
                    continue
                last_word = words[-1]
                if not re.fullmatch(r"[0-9a-fA-F]+", last_word):
                    continue
                word_val = int(last_word, 16)
                if (word_val & 1) != 0:
                    return True
            except (OSError, ValueError):
                continue

        return False

    def _check_logind(self) -> bool | None:
        """Queries systemd-logind via busctl for LidClosed property."""
        busctl_bin = shutil.which("busctl")
        if not busctl_bin:
            return None

        try:
            proc = subprocess.run(
                [
                    busctl_bin,
                    "--timeout=2",
                    "get-property",
                    "org.freedesktop.login1",
                    "/org/freedesktop/login1",
                    "org.freedesktop.login1.Manager",
                    "LidClosed",
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            if proc.returncode == 0:
                val = proc.stdout.strip()
                if val == "b true":
                    return True
                if val == "b false":
                    return False
        except (subprocess.SubprocessError, OSError):
            return None

        return None

    def check(self) -> ClamshellStatus:
        """Evaluates and returns comprehensive clamshell lid status."""
        # 1. ACPI lid reports its own state directly
        acpi_res = self._check_acpi()
        if acpi_res is not None:
            return acpi_res

        # 2. Input switch + logind fallback (Apple Silicon SMC, etc.)
        if self._has_input_lid_switch():
            logind_closed = self._check_logind()
            if logind_closed is not None:
                return ClamshellStatus(
                    is_closed=logind_closed,
                    lid_detected=True,
                    method_used="logind",
                    detail=f"Input SW_LID found; logind reported LidClosed={logind_closed}",
                )
            return ClamshellStatus(
                is_closed=False,
                lid_detected=True,
                method_used="input_switch",
                detail="Input SW_LID detected but logind query failed/timed out",
            )

        return ClamshellStatus(
            is_closed=False,
            lid_detected=False,
            method_used="none",
            detail="No laptop lid switch or ACPI button found (desktop or unsupported lid)",
        )

    def is_lid_closed(self) -> bool:
        """Returns True if the lid is confirmed closed, False otherwise."""
        return self.check().is_closed

    @classmethod
    def get_helper_script_source(cls) -> str:
        """Returns the high-speed bash implementation for /usr/local/bin/dusky-hw-laptop-closed."""
        return """#!/bin/bash
# ==============================================================================
# Dusky Clamshell (Lid-Closed) Gate Helper
# ==============================================================================
# Returns 0 (success) when the laptop lid is closed.
# Returns 1 when open or no lid switch exists.
# Used by PAM pam_exec.so to bypass pam_fprintd on clamshell setups.
# ==============================================================================

acpi_lid="${DUSKY_ACPI_LID_PATH:-/proc/acpi/button/lid}"
input_class="${DUSKY_INPUT_CLASS_PATH:-/sys/class/input}"

# 1. ACPI lid check
shopt -s nullglob
acpi_states=("$acpi_lid"/*/state)
shopt -u nullglob

found_open=false
for state in "${acpi_states[@]}"; do
  [[ -r "$state" ]] || continue
  content=$(<"$state")
  content=${content,,}
  [[ $content == *"closed"* ]] && exit 0
  [[ $content == *"open"* ]] && found_open=true
done
$found_open && exit 1

# 2. Input switch bitmap check for SW_LID (bit 0 of last word)
for sw in "$input_class"/*/capabilities/sw; do
  [[ -r "$sw" ]] || continue
  read -r -a words <"$sw" || true
  (( ${#words[@]} )) || continue
  word=${words[-1]}
  [[ "$word" =~ ^[0-9a-fA-F]+$ ]] || continue
  if (( 16#$word & 1 )); then
    lid_state=$(busctl --timeout=2 get-property org.freedesktop.login1 \\
      /org/freedesktop/login1 org.freedesktop.login1.Manager LidClosed 2>/dev/null)
    [[ "$lid_state" == "b true" ]] && exit 0
    exit 1
  fi
done

exit 1
"""


# =============================================================================
# 5. PRIVILEGED FILE & COMMAND HELPERS
# =============================================================================

class PrivilegedRunner:
    """Safely executes commands and writes system files with optional elevation."""

    @staticmethod
    def run_cmd(
        cmd: list[str],
        *,
        check: bool = True,
        capture_output: bool = True,
        stdin_input: str | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Runs a command, prepending sudo if non-root and not already in command."""
        full_cmd = [*cmd]
        if os.environ.get("DUSKY_NO_SUDO") != "1" and os.geteuid() != 0 and full_cmd[0] != "sudo":
            full_cmd = ["sudo", *full_cmd]

        return subprocess.run(
            full_cmd,
            input=stdin_input,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            check=check,
        )

    @staticmethod
    def write_file(
        target: Path,
        content: str,
        mode: int = 0o644,
        owner: str | None = None,
    ) -> None:
        """
        Writes a file atomically. If running unprivileged and writing to a
        system path, stages in /tmp and invokes sudo install.
        When running as root, preserves or assigns user ownership if owner is provided.
        """
        target = target.resolve()
        target_dir = target.parent
        # Find nearest existing ancestor directory to verify writability
        curr = target_dir
        while not curr.exists() and curr != curr.parent:
            curr = curr.parent

        if os.geteuid() == 0 or os.access(curr, os.W_OK):
            target_dir.mkdir(parents=True, exist_ok=True)
            if os.geteuid() == 0 and owner:
                try:
                    pw = pwd.getpwnam(owner)
                    p = target_dir
                    while p != Path("/") and p != Path(pw.pw_dir).parent:
                        if str(p).startswith(pw.pw_dir):
                            st = p.stat()
                            if st.st_uid == 0:
                                os.chown(p, pw.pw_uid, pw.pw_gid)
                        p = p.parent
                except (KeyError, OSError):
                    pass
            tmp_path = target_dir / f".tmp_{target.name}_{os.getpid()}_{time.time_ns()}"
            try:
                tmp_path.write_text(content, encoding="utf-8")
                tmp_path.chmod(mode)
                if os.geteuid() == 0 and owner:
                    try:
                        pw = pwd.getpwnam(owner)
                        os.chown(tmp_path, pw.pw_uid, pw.pw_gid)
                    except (KeyError, OSError):
                        pass
                tmp_path.replace(target)
            finally:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
        else:
            # Install beside the destination, then rename: failed copies must
            # never leave a truncated live PAM or lock configuration.
            suffix = f"{os.getpid()}_{time.time_ns()}"
            tmp_path = Path(f"/tmp/.stage_{target.name}_{suffix}")
            installed_tmp = target_dir / f".tmp_{target.name}_{suffix}"
            published = False
            try:
                tmp_path.write_text(content, encoding="utf-8")
                tmp_path.chmod(mode)
                install_cmd = ["install", f"-Dm{mode:o}"]
                if owner:
                    install_cmd.extend(["-o", owner])
                install_cmd.extend([str(tmp_path), str(installed_tmp)])
                PrivilegedRunner.run_cmd(install_cmd, check=True)
                PrivilegedRunner.run_cmd(["mv", "-fT", "--", str(installed_tmp), str(target)], check=True)
                published = True
            finally:
                try:
                    if not published:
                        PrivilegedRunner.run_cmd(["rm", "-f", "--", str(installed_tmp)], check=False)
                finally:
                    tmp_path.unlink(missing_ok=True)

    @staticmethod
    def remove_file(target: Path) -> bool:
        """Removes a file, using sudo if unprivileged."""
        target = target.resolve()
        if not target.exists():
            return False

        if os.geteuid() == 0 or os.access(target.parent, os.W_OK):
            try:
                target.unlink(missing_ok=True)
                return True
            except OSError:
                pass

        try:
            PrivilegedRunner.run_cmd(["rm", "-f", "--", str(target)], check=True)
            return True
        except (subprocess.SubprocessError, OSError):
            return False

    @staticmethod
    def remove_dir_if_empty(target: Path) -> bool:
        """Removes an empty directory, using sudo if unprivileged."""
        target = target.resolve()
        if not target.is_dir():
            return False

        if os.geteuid() == 0 or os.access(target.parent, os.W_OK):
            try:
                target.rmdir()
                return True
            except OSError:
                return False

        try:
            PrivilegedRunner.run_cmd(["rmdir", "--ignore-fail-on-non-empty", str(target)], check=False)
            return not target.is_dir()
        except (subprocess.SubprocessError, OSError):
            return False


# =============================================================================
# 6. PACKAGE MANAGEMENT & REPOSITORY RESOLUTION
# =============================================================================

class PackageManager:
    """Manages installation and verification of fprint packages via pacman."""

    @staticmethod
    def is_package_installed(pkg: str) -> bool:
        """Queries pacman -Q for a specific package."""
        if os.environ.get(ENV_MOCK_PACKAGES) == "1":
            return True
        pacman_bin = shutil.which("pacman")
        if not pacman_bin:
            return False
        res = subprocess.run(
            [pacman_bin, "-Q", "--", pkg],
            capture_output=True,
            text=True,
            check=False,
        )
        return res.returncode == 0

    @staticmethod
    def is_package_available(pkg: str) -> bool:
        """Queries pacman sync databases to check if a package is available."""
        if os.environ.get(ENV_MOCK_PACKAGES) == "1":
            return True
        pacman_bin = shutil.which("pacman")
        if not pacman_bin:
            return False
        res = subprocess.run(
            [pacman_bin, "-Si", "--", pkg],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return res.returncode == 0

    @classmethod
    def get_status(cls) -> dict[str, bool]:
        """Returns installed status of core fingerprint packages."""
        return {
            "fprintd": cls.is_package_installed("fprintd"),
            "libfprint-git": cls.is_package_installed("libfprint-git"),
            "libfprint": cls.is_package_installed("libfprint"),
            "usbutils": cls.is_package_installed("usbutils"),
        }

    @classmethod
    def is_fully_installed(cls) -> bool:
        """Checks if fprintd, a libfprint library, and usbutils are installed."""
        status = cls.get_status()
        has_lib = status["libfprint-git"] or status["libfprint"]
        return status["fprintd"] and status["usbutils"] and has_lib

    @classmethod
    def repair_broken_library_state(cls) -> tuple[bool, str]:
        """
        Repairs an incomplete installation where fprintd is installed without
        a backing libfprint library. Leaves existing valid drivers alone.
        """
        status = cls.get_status()
        if status["fprintd"] and not (status["libfprint-git"] or status["libfprint"]):
            return cls.install_required()
        return True, "Driver state is sound."

    @classmethod
    def install_required(cls, prefer_git: bool = False) -> tuple[bool, str]:
        """
        Installs missing packages. Uses '--ask 4' so pacman automatically accepts
        package replacements in a single transaction if replacing stock libfprint.
        """
        if cls.is_fully_installed():
            return True, "All required packages are already installed."

        pacman_bin = shutil.which("pacman")
        if not pacman_bin:
            return False, "pacman binary not found on the system."

        # Dynamically target available package: use libfprint-git if in repos, else official libfprint
        if prefer_git and cls.is_package_available("libfprint-git"):
            lib_target = "libfprint-git"
        else:
            lib_target = "libfprint"

        pkgs_to_install = [lib_target, "fprintd", "usbutils"]
        cmd = [
            "pacman",
            "-S",
            "--needed",
            "--noconfirm",
            "--ask",
            "4",
            "--",
            *pkgs_to_install,
        ]

        try:
            is_interactive = sys.stdin.isatty() and sys.stdout.isatty()
            proc = PrivilegedRunner.run_cmd(cmd, check=False, capture_output=not is_interactive)
            if proc.returncode != 0:
                err_text = (proc.stderr or proc.stdout or f"Exit code {proc.returncode}").strip()
                return False, f"Package installation failed: {err_text}"
            return True, f"Successfully verified/installed {', '.join(pkgs_to_install)}."
        except (subprocess.SubprocessError, OSError) as err:
            return False, f"Failed to execute pacman: {err}"

    @classmethod
    def drop_packages(cls) -> tuple[bool, str]:
        """Removes installed fingerprint packages."""
        pacman_bin = shutil.which("pacman")
        if not pacman_bin:
            return False, "pacman binary not found."

        installed = [pkg for pkg in ["fprintd", "libfprint-git", "libfprint"] if cls.is_package_installed(pkg)]
        if not installed:
            return True, "No fingerprint packages to remove."

        cmd = ["pacman", "-R", "--noconfirm", "--", *installed]
        try:
            proc = PrivilegedRunner.run_cmd(cmd, check=False)
            if proc.returncode != 0:
                return False, f"Failed to remove packages: {proc.stderr.strip()}"
            return True, f"Removed packages: {', '.join(installed)}"
        except (subprocess.SubprocessError, OSError) as err:
            return False, f"Package drop failed: {err}"


# =============================================================================
# 7. PAM CONFIGURATION STACK (SUDO & POLKIT)
# =============================================================================

class PamConfigurator:
    """
    Safely edits /etc/pam.d/sudo and /etc/pam.d/polkit-1 with clamshell gating.
    All edits are atomic and idempotent.
    """

    def __init__(self, pam_dir: Path | None = None) -> None:
        self.pam_dir = pam_dir or Path(os.environ.get(ENV_PAM_DIR, str(DEFAULT_PAM_DIR)))

    def _get_sudo_path(self) -> Path:
        return self.pam_dir / "sudo"

    def _get_polkit_path(self) -> Path:
        return self.pam_dir / "polkit-1"

    def is_sudo_configured(self) -> bool:
        """Checks if both pam_fprintd and the clamshell gate are active in sudo."""
        return self._is_configured(self._get_sudo_path())

    def is_polkit_configured(self) -> bool:
        """Checks if both pam_fprintd and the clamshell gate are active in polkit."""
        return self._is_configured(self._get_polkit_path())

    @staticmethod
    def _is_configured(path: Path) -> bool:
        """Require adjacent active rules: success=1 must skip exactly pam_fprintd."""
        if not path.is_file():
            return False
        try:
            text = path.read_text(encoding="utf-8")
            rules = [line.split("#", 1)[0].split() for line in text.splitlines()]
            rules = [rule for rule in rules if rule]
            expected = [get_pam_gate_rule().split(), PAM_FPRINT_RULE.split()]
            return any(rules[i:i + 2] == expected for i in range(len(rules) - 1))
        except OSError:
            return False

    @staticmethod
    def _is_fingerprint_rule(line: str) -> bool:
        fields = line.split("#", 1)[0].split()
        if not fields or fields[0] not in {"auth", "-auth"}:
            return False
        return "pam_fprintd.so" in fields or (
            "pam_exec.so" in fields and any(
                Path(field).name in {"dusky-hw-laptop-closed", "omarchy-hw-laptop-closed"}
                for field in fields
            )
        )

    def configure_sudo(self) -> tuple[bool, str]:
        """
        Inserts the clamshell gate immediately before pam_fprintd.so at the
        top of /etc/pam.d/sudo.
        """
        path = self._get_sudo_path()
        if not path.is_file():
            return False, f"PAM sudo config not found: {path}"

        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except OSError as err:
            return False, f"Failed to read {path}: {err}"

        # Clean existing legacy or misordered fprint lines first to guarantee order
        cleaned: list[str] = []
        for line in lines:
            if self._is_fingerprint_rule(line):
                continue
            cleaned.append(line)

        # Insert gate and fprintd rule before first auth rule or at start
        insert_idx = 0
        for i, line in enumerate(cleaned):
            s = line.strip()
            if s.startswith("auth") and not s.startswith("#"):
                insert_idx = i
                break

        rule_block = [f"{get_pam_gate_rule()}\n", f"{PAM_FPRINT_RULE}\n"]
        new_lines = [*cleaned[:insert_idx], *rule_block, *cleaned[insert_idx:]]

        try:
            PrivilegedRunner.write_file(path, "".join(new_lines), mode=0o644)
            return True, "Configured /etc/pam.d/sudo for fingerprint authentication."
        except Exception as err:
            return False, f"Failed to write {path}: {err}"

    def configure_polkit(self) -> tuple[bool, str]:
        """
        Configures /etc/pam.d/polkit-1 with clamshell gate and pam_fprintd.
        Creates a clean PAM stack if polkit-1 does not exist.
        """
        path = self._get_polkit_path()

        if path.is_file():
            try:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            except OSError as err:
                return False, f"Failed to read {path}: {err}"

            cleaned = [
                l for l in lines
                if not self._is_fingerprint_rule(l)
            ]
            insert_idx = 0
            for i, line in enumerate(cleaned):
                s = line.strip()
                if s.startswith("auth") and not s.startswith("#"):
                    insert_idx = i
                    break

            new_lines = [*cleaned[:insert_idx], f"{get_pam_gate_rule()}\n", f"{PAM_FPRINT_RULE}\n", *cleaned[insert_idx:]]
            content = "".join(new_lines)
        else:
            content = (
                "#%PAM-1.0\n"
                f"{get_pam_gate_rule()}\n"
                f"{PAM_FPRINT_RULE}\n"
                "auth      include    system-auth\n\n"
                "account   include    system-auth\n"
                "password  include    system-auth\n"
                "session   include    system-auth\n"
            )

        try:
            PrivilegedRunner.write_file(path, content, mode=0o644)
            return True, "Configured /etc/pam.d/polkit-1 for fingerprint authentication."
        except Exception as err:
            return False, f"Failed to write {path}: {err}"

    def remove_pam_configs(self) -> tuple[bool, str]:
        """Removes all fingerprint and clamshell gate rules from PAM stacks."""
        sudo_path = self._get_sudo_path()
        polkit_path = self._get_polkit_path()
        errors: list[str] = []

        if sudo_path.is_file():
            try:
                lines = sudo_path.read_text(encoding="utf-8").splitlines(keepends=True)
                filtered = [
                    l for l in lines
                    if not self._is_fingerprint_rule(l)
                ]
                if len(filtered) != len(lines):
                    PrivilegedRunner.write_file(sudo_path, "".join(filtered), mode=0o644)
            except Exception as err:
                errors.append(f"sudo PAM: {err}")

        if polkit_path.is_file():
            try:
                lines = polkit_path.read_text(encoding="utf-8").splitlines(keepends=True)
                filtered = [
                    l for l in lines
                    if not self._is_fingerprint_rule(l)
                ]
                # If polkit-1 only has comments and includes that we wrote, remove or clean it
                non_empty = [l.strip() for l in filtered if l.strip() and not l.strip().startswith("#")]
                if not non_empty:
                    if not PrivilegedRunner.remove_file(polkit_path):
                        errors.append(f"Failed to remove {polkit_path}")
                elif len(filtered) != len(lines):
                    PrivilegedRunner.write_file(polkit_path, "".join(filtered), mode=0o644)
            except Exception as err:
                errors.append(f"polkit PAM: {err}")

        if errors:
            return False, "; ".join(errors)
        return True, "Removed fingerprint configuration from PAM stacks."


# =============================================================================
# 8. SYSTEMD RECOVERY & RESUME HOOKS
# =============================================================================

class SystemdRecoveryManager:
    """
    Installs suspend/resume hooks and timeout overrides for fprintd.
    Suspend can wedge fprintd's USB interface claim; restarting it on resume
    clears stale handles so unlocking works immediately without hanging.
    """

    def __init__(
        self,
        sleep_dir: Path | None = None,
        service_dir: Path | None = None,
    ) -> None:
        self.sleep_dir = sleep_dir or Path(
            os.environ.get(ENV_SYSTEMD_SLEEP_DIR, str(DEFAULT_SYSTEMD_SLEEP_DIR))
        )
        self.service_dir = service_dir or Path(
            os.environ.get(ENV_SYSTEMD_SERVICE_DIR, str(DEFAULT_SYSTEMD_SERVICE_DIR))
        )

    def _get_resume_hook_path(self) -> Path:
        return self.sleep_dir / "fprintd-resume"

    def _get_dropin_path(self) -> Path:
        return self.service_dir / "fprintd.service.d" / "10-stop-timeout.conf"

    def is_resume_hook_installed(self) -> bool:
        return self._get_resume_hook_path().is_file()

    def is_stop_timeout_installed(self) -> bool:
        return self._get_dropin_path().is_file()

    def install(self) -> tuple[bool, str]:
        """Installs resume recovery hook and service stop timeout bound."""
        resume_content = (
            "#!/bin/bash\n"
            "# Suspend can wedge fprintd's claim; restart it after resume to clear it.\n"
            "# Upstream: https://gitlab.freedesktop.org/libfprint/fprintd/-/issues/173\n"
            'if [[ $1 == "post" ]]; then\n'
            "  systemctl --no-block try-restart fprintd.service 2>/dev/null || true\n"
            "fi\n"
        )
        stop_timeout_content = (
            "# A stale device handle can make fprintd ignore SIGTERM after resume.\n"
            "# The numeric prefix lets an administrator's override.conf take precedence.\n"
            "[Service]\n"
            "TimeoutStopSec=3s\n"
        )

        try:
            PrivilegedRunner.write_file(self._get_resume_hook_path(), resume_content, mode=0o755)
            PrivilegedRunner.write_file(self._get_dropin_path(), stop_timeout_content, mode=0o644)
            if os.environ.get("DUSKY_MOCK_SYSTEMD") != "1":
                PrivilegedRunner.run_cmd(["systemctl", "daemon-reload"], check=True)
            return True, "Installed fprintd resume recovery hook and 3s stop timeout."
        except Exception as err:
            return False, f"Failed to install systemd recovery hooks: {err}"

    def remove(self) -> tuple[bool, str]:
        """Removes resume recovery hook and service drop-in override."""
        hook_path = self._get_resume_hook_path()
        dropin_path = self._get_dropin_path()

        try:
            for path in (hook_path, dropin_path):
                if path.exists() and not PrivilegedRunner.remove_file(path):
                    return False, f"Failed to remove {path}"
            PrivilegedRunner.remove_dir_if_empty(dropin_path.parent)
            if os.environ.get("DUSKY_MOCK_SYSTEMD") != "1":
                PrivilegedRunner.run_cmd(["systemctl", "daemon-reload"], check=True)
        except (subprocess.SubprocessError, OSError) as err:
            return False, f"Failed to remove systemd recovery hooks: {err}"
        return True, "Removed systemd resume hooks and service overrides."


# =============================================================================
# 9. HYPRLOCK INTEGRATION ENGINE
# =============================================================================

class HyprlockConfigurator:
    """
    Manages native Hyprlock fingerprint configuration.
    Injects an auth { fingerprint { ... } } configuration block into hyprlock.conf.
    Preserves theme sources and is 100% compatible with Dusky TUI theme switching.
    """

    FPRINT_BLOCK_START: Final = "# --- DUSKY HYPRLOCK FINGERPRINT CONFIG (AUTO-MANAGED) ---"
    FPRINT_BLOCK_END: Final = "# --- END DUSKY HYPRLOCK FINGERPRINT CONFIG ---"

    def __init__(self, hypr_dir: Path | None = None, user: str | None = None) -> None:
        self.user = user
        if hypr_dir:
            self.hypr_dir = hypr_dir
        else:
            env_val = os.environ.get(ENV_HYPR_DIR)
            if env_val:
                self.hypr_dir = Path(env_val)
            else:
                user_home = IdentityResolver.resolve_user_home(user)
                self.hypr_dir = user_home / ".config" / "hypr"

    def _get_hyprlock_conf(self) -> Path:
        return self.hypr_dir / "hyprlock.conf"

    def _get_fingerprint_conf(self) -> Path:
        return self.hypr_dir / "fingerprint.conf"

    @classmethod
    def generate_auth_block(cls, enabled: bool = True) -> str:
        """Generates the native Hyprlock auth block syntax."""
        state_str = "true" if enabled else "false"
        return (
            f"{cls.FPRINT_BLOCK_START}\n"
            "auth {\n"
            "    fingerprint {\n"
            f"        enabled = {state_str}\n"
            "        ready_message = (Scan fingerprint to unlock)\n"
            "        present_message = Scanning fingerprint...\n"
            "        retry_delay = 250 # milliseconds\n"
            "    }\n"
            "}\n"
            f"{cls.FPRINT_BLOCK_END}\n"
        )

    def is_configured(self) -> bool:
        """Returns True if fingerprint auth is actively enabled in hyprlock.conf."""
        conf = self._get_hyprlock_conf()
        if not conf.is_file():
            return False
        try:
            content = conf.read_text(encoding="utf-8")
            self._strip_managed_blocks(content.splitlines(keepends=True))
            for block in content.split(self.FPRINT_BLOCK_START)[1:]:
                managed = block.split(self.FPRINT_BLOCK_END, 1)[0]
                if re.search(r"(?m)^\s*enabled\s*=\s*true\s*(?:#.*)?$", managed):
                    return True
            return False
        except (OSError, ValueError):
            return False

    @classmethod
    def _strip_managed_blocks(cls, lines: list[str]) -> list[str]:
        """Remove complete managed blocks, refusing to discard an unfinished tail."""
        cleaned: list[str] = []
        inside = False
        for line in lines:
            marker = line.strip()
            if marker == cls.FPRINT_BLOCK_START:
                if inside:
                    raise ValueError("Nested Hyprlock fingerprint markers; repair the managed block first.")
                inside = True
            elif marker == cls.FPRINT_BLOCK_END:
                if not inside:
                    raise ValueError("Unmatched Hyprlock fingerprint end marker; repair the managed block first.")
                inside = False
            elif not inside:
                cleaned.append(line)
        if inside:
            raise ValueError("Unfinished Hyprlock fingerprint block; repair the managed block first.")
        return cleaned

    def configure(self, enabled: bool = True) -> tuple[bool, str]:
        """Applies or updates the native fingerprint configuration."""
        conf = self._get_hyprlock_conf()
        target_user = self.user or IdentityResolver.resolve_target_user()

        auth_block = self.generate_auth_block(enabled=enabled)

        # Merge directly into the active config; no separate include is needed.
        existing_lines: list[str] = []
        if conf.is_file():
            try:
                existing_lines = conf.read_text(encoding="utf-8").splitlines(keepends=True)
            except OSError as err:
                return False, f"Failed to read {conf}: {err}"

        try:
            cleaned_lines = self._strip_managed_blocks(existing_lines)
        except ValueError as err:
            return False, str(err)

        # Append fresh auth block
        final_text = "".join(cleaned_lines).rstrip() + "\n\n" + auth_block
        try:
            PrivilegedRunner.write_file(conf, final_text, mode=0o644, owner=target_user)
            return True, "Configured Hyprlock native fingerprint authentication."
        except Exception as err:
            return False, f"Failed to update {conf}: {err}"

    def remove(self) -> tuple[bool, str]:
        """Removes the fingerprint configuration from Hyprlock."""
        conf = self._get_hyprlock_conf()
        fprint_file = self._get_fingerprint_conf()
        target_user = self.user or IdentityResolver.resolve_target_user()

        if fprint_file.exists() and not PrivilegedRunner.remove_file(fprint_file):
            return False, f"Failed to remove {fprint_file}"

        if not conf.is_file():
            return True, "Hyprlock configuration already absent."

        try:
            lines = conf.read_text(encoding="utf-8").splitlines(keepends=True)
            cleaned_lines = self._strip_managed_blocks(lines)

            final_text = "".join(cleaned_lines).rstrip() + "\n"
            PrivilegedRunner.write_file(conf, final_text, mode=0o644, owner=target_user)
            return True, "Removed fingerprint configuration from Hyprlock."
        except Exception as err:
            return False, f"Failed to clean {conf}: {err}"


# =============================================================================
# 10. ENROLLMENT & PROBE VERIFICATION
# =============================================================================

class ProbeClassifier:
    """
    Strict probe classifier for fprintd-list output.
    Only definitive answers change state (#9453).
    """

    ENROLL_ROW_RE: Final = re.compile(r"^\s*-\s*#\d+:\s*\S+", re.MULTILINE)
    NO_FINGERS_RE: Final = re.compile(r"has no fingers enrolled", re.IGNORECASE)

    @classmethod
    def classify(cls, output: str) -> str:
        """Classifies probe output as 'yes', 'no', or 'unknown'."""
        if not output or not output.strip():
            return "unknown"

        s = output.strip()
        if s == "no":
            return "no"

        if cls.ENROLL_ROW_RE.search(output):
            return "yes"

        if cls.NO_FINGERS_RE.search(output):
            return "no"

        return "unknown"

    @classmethod
    def extract_enrolled_fingers(cls, output: str) -> list[str]:
        """Extracts list of enrolled finger identifiers from fprintd-list."""
        fingers: list[str] = []
        for line in output.splitlines():
            m = re.prefixmatch(r"\s*-\s*#\d+:\s*([a-zA-Z0-9_-]+)", line)
            if m:
                fingers.append(m.group(1))
        return fingers


class EnrollmentManager:
    """Orchestrates fprintd enrollment and verification operations."""

    def __init__(self, fprint_dir: Path | None = None) -> None:
        self.fprint_dir = fprint_dir or Path(
            os.environ.get(ENV_FPRINT_DIR, str(DEFAULT_FPRINT_DIR))
        )

    def list_enrolled(self, user: str) -> tuple[str, list[str]]:
        """
        Executes fprintd-list for user and classifies the outcome.
        Returns (classification, finger_list).
        """
        fprintd_list_bin = shutil.which("fprintd-list")
        if not fprintd_list_bin or not os.path.exists(fprintd_list_bin):
            return "no", []

        try:
            res = subprocess.run(
                [fprintd_list_bin, user],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode != 0:
                return "unknown", []
            combined_output = (res.stdout + "\n" + res.stderr).strip()
            cls_result = ProbeClassifier.classify(combined_output)
            fingers = ProbeClassifier.extract_enrolled_fingers(combined_output)
            return cls_result, fingers
        except (subprocess.SubprocessError, OSError):
            return "unknown", []

    def enroll(
        self,
        user: str,
        finger: str = "right-index-finger",
        interactive: bool = True,
    ) -> tuple[bool, str]:
        """Invokes fprintd-enroll for the specified user and finger."""
        fprintd_enroll_bin = shutil.which("fprintd-enroll")
        if not fprintd_enroll_bin:
            return False, "fprintd-enroll executable not found on the system."

        cmd = [fprintd_enroll_bin, "-f", finger, user]
        try:
            proc = PrivilegedRunner.run_cmd(cmd, check=False, capture_output=not interactive)
            if proc.returncode == 0:
                return True, f"Fingerprint enrolled successfully for {user} ({finger})."
            err_msg = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
            return False, f"Enrollment exited with status {proc.returncode}: {err_msg}"
        except Exception as err:
            return False, f"Failed to execute fprintd-enroll: {err}"

    def verify(
        self,
        user: str,
        finger: str | None = None,
        interactive: bool = True,
    ) -> tuple[bool, str]:
        """Runs fprintd-verify to validate enrolled prints."""
        fprintd_verify_bin = shutil.which("fprintd-verify")
        if not fprintd_verify_bin:
            return False, "fprintd-verify executable not found."

        cmd = [fprintd_verify_bin]
        if finger:
            cmd.extend(["-f", finger])
        cmd.append(user)

        try:
            proc = PrivilegedRunner.run_cmd(cmd, check=False, capture_output=not interactive, timeout=20)
            if proc.returncode == 0:
                return True, f"Fingerprint verification passed for {user}."
            err_msg = proc.stderr.strip() if proc.stderr else f"Exit code {proc.returncode}"
            return False, f"Verification failed (code {proc.returncode}): {err_msg}"
        except Exception as err:
            return False, f"Failed to run fprintd-verify: {err}"

    def delete_user_prints(self, user: str) -> tuple[bool, str]:
        """Deletes /var/lib/fprint/<user> directory cleanly and safely."""
        IdentityResolver.validate_username(user)
        user_dir = self.fprint_dir / user

        if not user_dir.exists():
            return True, f"No local fingerprints found at {user_dir}"

        try:
            PrivilegedRunner.run_cmd(["rm", "-rf", "--", str(user_dir)], check=True)
            return True, f"Deleted local saved fingerprints for {user}."
        except Exception as err:
            return False, f"Failed to remove {user_dir}: {err}"


# =============================================================================
# 11. FULL SYSTEM ORCHESTRATOR
# =============================================================================

class FingerprintOrchestrator:
    """Master orchestrator for detection, setup, verification, and teardown."""

    def __init__(self, simulate: bool = False, user: str | None = None) -> None:
        self.simulate = simulate or (os.environ.get(ENV_SIMULATE_SENSOR) == "1")
        self.user = user
        self.detector = HardwareDetector(simulate=self.simulate)
        self.clamshell = ClamshellGate()
        self.pam = PamConfigurator()
        self.systemd = SystemdRecoveryManager()
        self.hyprlock = HyprlockConfigurator(user=self.user)
        self.enrollment = EnrollmentManager()

    def get_system_status(self, user: str | None = None) -> SystemStatus:
        """Collects and returns comprehensive status across all subsystems."""
        target_user = IdentityResolver.validate_username(user or self.user) if (user or self.user) else IdentityResolver.resolve_target_user()
        if self.hyprlock.user != target_user:
            self.hyprlock = HyprlockConfigurator(user=target_user)
        readers = self.detector.detect_readers()
        probe_cls, fingers = self.enrollment.list_enrolled(target_user)
        lid_info = self.clamshell.check()

        systemctl_bin = shutil.which("systemctl")
        fprintd_active = False
        if systemctl_bin:
            res = subprocess.run(
                [systemctl_bin, "is-active", "fprintd.service"],
                capture_output=True,
                text=True,
                check=False,
            )
            fprintd_active = (res.stdout.strip() == "active")

        return SystemStatus(
            hardware_present=len(readers) > 0,
            devices=[r.to_dict() for r in readers],
            packages_installed=PackageManager.get_status(),
            fprintd_service_active=fprintd_active,
            sudo_pam_configured=self.pam.is_sudo_configured(),
            polkit_pam_configured=self.pam.is_polkit_configured(),
            clamshell_gate_installed=Path(get_clamshell_gate_bin()).is_file(),
            resume_hook_installed=self.systemd.is_resume_hook_installed(),
            stop_timeout_installed=self.systemd.is_stop_timeout_installed(),
            hyprlock_configured=self.hyprlock.is_configured(),
            enrolled_fingers=fingers,
            probe_classification=probe_cls,
            lid_status=dataclasses.asdict(lid_info),
        )

    def install_clamshell_gate(self) -> tuple[bool, str]:
        """Installs /usr/local/bin/dusky-hw-laptop-closed."""
        target = Path(get_clamshell_gate_bin())
        content = ClamshellGate.get_helper_script_source()
        try:
            PrivilegedRunner.write_file(target, content, mode=0o755)
            return True, f"Installed clamshell gate to {get_clamshell_gate_bin()}"
        except Exception as err:
            return False, f"Failed to install clamshell gate: {err}"

    def run_setup(
        self,
        *,
        user: str | None = None,
        finger: str = "right-index-finger",
        force: bool = False,
        skip_enroll: bool = False,
        interactive: bool = True,
        simulate: bool = False,
    ) -> tuple[bool, str]:
        """
        Orchestrates full setup workflow:
        0. Hardware/mode checks, then sudo authorization
        1. Hardware probe (or simulation)
        2. Package installation
        3. Enrollment & verification
        4. Clamshell gate & systemd recovery installation
        5. PAM and Hyprlock configuration
        """
        target_user = IdentityResolver.validate_username(user or self.user) if (user or self.user) else IdentityResolver.resolve_target_user()
        if self.hyprlock.user != target_user:
            self.hyprlock = HyprlockConfigurator(user=target_user)
        print(f"\033[1;34m[::]\033[0m Initiating Fingerprint Setup for user: \033[1m{target_user}\033[0m")

        # Step 1: Hardware Detection
        is_sim = simulate or self.simulate
        if is_sim:
            readers = HardwareDetector(simulate=True).detect_readers()
        else:
            readers = self.detector.detect_readers()

        if not readers and not force:
            return False, "No fingerprint sensor detected on USB bus. (Use --force or --simulate to proceed)"

        if readers:
            r = readers[0]
            print(f"\033[1;32m[✓]\033[0m Detected hardware: {r.product_name} [{r.vendor_id}:{r.product_id}] via {r.matched_by}")
        else:
            print("\033[1;33m[!]\033[0m Forcing setup without physical reader attached.")

        if not is_sim and not skip_enroll and not interactive:
            return False, "Interactive terminal required for fingerprint enrollment."
        if not ensure_sudo():
            return False, "Sudo authorization failed or was refused. Administrative privileges are required."

        # Step 2: Ensure Packages
        print("\033[1;34m[::]\033[0m Verifying prerequisite packages...")
        pkg_ok, pkg_msg = PackageManager.install_required()
        if not pkg_ok:
            return False, f"Package setup error: {pkg_msg}"
        print(f"\033[1;32m[✓]\033[0m {pkg_msg}")

        # Step 3: Enrollment & Verification
        if not skip_enroll:
            if is_sim:
                print(f"\n\033[1;32m[✓]\033[0m Simulation Mode: Simulated successful enrollment of '{finger}' for '{target_user}'.")
                print("\033[1;32m[✓]\033[0m Simulation Mode: Verified simulated fingerprint.")
            else:
                print(f"\n\033[1;32m[::]\033[0m Enrolling '{finger}' for user '{target_user}'.")
                print("Keep swiping or touching the sensor until the process completes...\n")
                enroll_ok, enroll_msg = self.enrollment.enroll(target_user, finger=finger, interactive=interactive)
                if not enroll_ok:
                    return False, f"Enrollment aborted: {enroll_msg}"
                print(f"\033[1;32m[✓]\033[0m {enroll_msg}")

                print("\n\033[1;34m[::]\033[0m Verifying enrolled fingerprint...")
                verify_ok, verify_msg = self.enrollment.verify(target_user, finger=finger, interactive=interactive)
                if not verify_ok:
                    return False, f"Verification failed: {verify_msg}"
                print(f"\033[1;32m[✓]\033[0m {verify_msg}")

        # Step 4: Install Clamshell Gate Helper
        gate_ok, gate_msg = self.install_clamshell_gate()
        if not gate_ok:
            return False, f"Gate setup error: {gate_msg}"
        print(f"\033[1;32m[✓]\033[0m {gate_msg}")

        # Step 5: Systemd Recovery Hooks
        sysd_ok, sysd_msg = self.systemd.install()
        if not sysd_ok:
            return False, f"Systemd setup error: {sysd_msg}"
        print(f"\033[1;32m[✓]\033[0m {sysd_msg}")

        # Step 6: PAM Stack Integration
        sudo_ok, sudo_msg = self.pam.configure_sudo()
        if not sudo_ok:
            return False, f"Sudo PAM error: {sudo_msg}"
        print(f"\033[1;32m[✓]\033[0m {sudo_msg}")

        polkit_ok, polkit_msg = self.pam.configure_polkit()
        if not polkit_ok:
            return False, f"Polkit PAM error: {polkit_msg}"
        print(f"\033[1;32m[✓]\033[0m {polkit_msg}")

        # Step 7: Hyprlock Configuration
        hypr_ok, hypr_msg = self.hyprlock.configure(enabled=True)
        if not hypr_ok:
            return False, f"Hyprlock setup error: {hypr_msg}"
        print(f"\033[1;32m[✓]\033[0m {hypr_msg}")

        return True, (
            "Fingerprint authentication configured successfully!\n"
            "Active across sudo, polkit, and Hyprlock lock screen."
        )

    def run_removal(self, user: str | None = None, purge_packages: bool = False) -> tuple[bool, str]:
        """Rolls back all PAM changes, Hyprlock configuration, hooks, and local prints."""
        target_user = IdentityResolver.validate_username(user or self.user) if (user or self.user) else IdentityResolver.resolve_target_user()
        if self.hyprlock.user != target_user:
            self.hyprlock = HyprlockConfigurator(user=target_user)
        print(f"\033[1;33m[::]\033[0m Removing fingerprint configuration for {target_user}...")

        # Step 0: Sudo validation upfront
        if not ensure_sudo():
            return False, "Sudo authorization failed or was refused. Administrative privileges are required."

        # 1. Rollback PAM
        pam_ok, pam_msg = self.pam.remove_pam_configs()
        print(f"[{'✓' if pam_ok else '!'}] {pam_msg}")

        # 2. Rollback Hyprlock
        hypr_ok, hypr_msg = self.hyprlock.remove()
        print(f"[{'✓' if hypr_ok else '!'}] {hypr_msg}")

        # 3. Rollback Systemd Hooks
        sysd_ok, sysd_msg = self.systemd.remove()
        print(f"[{'✓' if sysd_ok else '!'}] {sysd_msg}")

        # 4. Keep the helper available if a PAM cleanup failed.
        gate_ok = True
        gate_path = Path(get_clamshell_gate_bin())
        if pam_ok and gate_path.exists():
            gate_ok = PrivilegedRunner.remove_file(gate_path)

        # 5. Purge local saved prints
        del_ok, del_msg = self.enrollment.delete_user_prints(target_user)
        print(f"[{'✓' if del_ok else '!'}] {del_msg}")
        if not del_ok:
            return False, f"Failed to delete fingerprints for {target_user}: {del_msg}"

        # 6. Optional package purge
        if not all((pam_ok, hypr_ok, sysd_ok, gate_ok)):
            return False, "Fingerprint cleanup incomplete; review the reported errors and retry."

        if purge_packages:
            print("\033[1;34m[::]\033[0m Purging fingerprint packages...")
            pkg_ok, pkg_msg = PackageManager.drop_packages()
            print(f"[{'✓' if pkg_ok else '!'}] {pkg_msg}")
            if not pkg_ok:
                return False, f"Package purge failed: {pkg_msg}"

        return True, f"Fingerprint authentication and {target_user}'s prints have been removed."


class InvitationManager:
    """Manages first-run fingerprint setup invitation notifications."""

    @classmethod
    def get_state_dir(cls, user: str | None = None) -> Path:
        """Returns the state directory for the user, safely resolving home directory."""
        user_home = IdentityResolver.resolve_user_home(user)
        return user_home / ".local" / "state" / "dusky"

    @classmethod
    def get_marker_file(cls, user: str | None = None) -> Path:
        """Returns the marker file path, respecting DUSKY_INVITED_MARKER for testing."""
        env_val = os.environ.get("DUSKY_INVITED_MARKER")
        if env_val:
            return Path(env_val)
        return cls.get_state_dir(user) / "fingerprint-setup-invited"

    @classmethod
    def should_invite(cls, orchestrator: FingerprintOrchestrator, user: str | None = None) -> bool:
        """Returns True if hardware is present, not yet configured, and not yet invited."""
        target_user = user or orchestrator.user or IdentityResolver.resolve_target_user()
        if not orchestrator.detector.has_reader():
            return False
        if orchestrator.pam.is_sudo_configured() and orchestrator.hyprlock.is_configured():
            return False
        if cls.get_marker_file(target_user).is_file():
            return False
        return True

    @classmethod
    def send_invitation(cls, orchestrator: FingerprintOrchestrator, user: str | None = None, force: bool = False) -> bool:
        """Sends a notification inviting the user to configure fingerprint authentication."""
        target_user = user or orchestrator.user or IdentityResolver.resolve_target_user()
        if not force and not cls.should_invite(orchestrator, user=target_user):
            return False

        notify_bin = shutil.which("notify-send")
        if not notify_bin:
            return False

        try:
            result = subprocess.run(
                [
                    notify_bin,
                    "-u", "normal",
                    "-a", "Dusky Fingerprint",
                    "Fingerprint Reader Detected",
                    "Enable sudo and screen unlocking with your fingerprint.\nRun setup_fingerprint.py setup",
                ],
                check=False,
            )
            if result.returncode != 0:
                return False
            marker = cls.get_marker_file(target_user)
            PrivilegedRunner.write_file(marker, "", mode=0o644, owner=target_user)
            return True
        except OSError:
            return False


# =============================================================================
# 12. CLI INTERFACE
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    """Builds the comprehensive CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="setup_fingerprint.py",
        description="Dusky Hyprlock Fingerprint Authentication Engine",
    )
    parser.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS, help="Simulate presence of fingerprint reader")
    parser.add_argument("--user", default=argparse.SUPPRESS, help="Target system user (default: current logged in user)")

    subparsers = parser.add_subparsers(dest="subcommand", help="Operational subcommands")

    # setup
    p_setup = subparsers.add_parser("setup", help="Configure fingerprint authentication (default)")
    p_setup.add_argument("--finger", default="right-index-finger", help="Finger to enroll (default: right-index-finger)")
    p_setup.add_argument("--force", action="store_true", help="Force configuration even if hardware is not detected")
    p_setup.add_argument("--skip-enroll", action="store_true", help="Skip enrollment and only configure software/PAM stacks")
    p_setup.add_argument("--non-interactive", action="store_true", help="Non-interactive mode (requires --skip-enroll)")
    p_setup.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS, help="Simulate fingerprint reader and enrollment")
    p_setup.add_argument("--user", default=argparse.SUPPRESS, help="Target system user")

    # detect
    p_detect = subparsers.add_parser("detect", help="Scan USB sysfs for fingerprint readers")
    p_detect.add_argument("--json", action="store_true", help="Output results in JSON format")
    p_detect.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS, help="Simulate detection of a fingerprint reader")

    # clamshell
    p_clamshell = subparsers.add_parser("clamshell", help="Check laptop lid state (exit 0=closed, 1=open)")
    p_clamshell.add_argument("--json", action="store_true", help="Output details in JSON format")
    p_clamshell.add_argument("--quiet", "-q", action="store_true", help="Suppress output (exit code only)")

    # status
    p_status = subparsers.add_parser("status", help="Display full fingerprint system status")
    p_status.add_argument("--json", action="store_true", help="Output full report in JSON format")
    p_status.add_argument("--user", default=argparse.SUPPRESS, help="Target system user")
    p_status.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS, help="Simulate reader presence in status check")

    # enroll
    p_enroll = subparsers.add_parser("enroll", help="Enroll a fingerprint for the target user")
    p_enroll.add_argument("--finger", default="right-index-finger", help="Target finger name")
    p_enroll.add_argument("--user", default=argparse.SUPPRESS, help="Target system user")

    # verify
    p_verify = subparsers.add_parser("verify", help="Verify enrolled fingerprint")
    p_verify.add_argument("--finger", default=None, help="Target finger name (optional)")
    p_verify.add_argument("--user", default=argparse.SUPPRESS, help="Target system user")

    # remove
    p_remove = subparsers.add_parser("remove", help="Remove fingerprint configuration and roll back PAM")
    p_remove.add_argument("--purge", action="store_true", help="Also drop fprintd and libfprint packages")
    p_remove.add_argument("--user", default=argparse.SUPPRESS, help="Target system user")

    # should-invite
    p_invite_check = subparsers.add_parser("should-invite", help="Check if setup invitation notification should be displayed")
    p_invite_check.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS, help="Simulate fingerprint reader")

    # invite
    p_invite = subparsers.add_parser("invite", help="Send desktop notification inviting user to setup fingerprint")
    p_invite.add_argument("--simulate", action="store_true", default=argparse.SUPPRESS, help="Simulate fingerprint reader")
    p_invite.add_argument("--force", action="store_true", help="Bypass invited check marker")

    # install-gate
    subparsers.add_parser("install-gate", help="Install high-speed clamshell PAM gate helper")

    # test
    p_test = subparsers.add_parser("test", help="Run comprehensive unit and stress-test suite")
    p_test.add_argument("-v", "--verbose", action="store_true", help="Verbose test reporting")

    return parser


def main() -> int:
    """CLI Entry Point."""
    parser = build_parser()
    args = parser.parse_args()

    simulate = getattr(args, "simulate", False)
    user = getattr(args, "user", None)

    orchestrator = FingerprintOrchestrator(simulate=simulate, user=user)
    subcommand = args.subcommand or "setup"

    match subcommand:
        case "setup":
            force = getattr(args, "force", False)
            skip_enroll = getattr(args, "skip_enroll", False)
            non_interactive = getattr(args, "non_interactive", False)
            finger = getattr(args, "finger", "right-index-finger")

            ok, msg = orchestrator.run_setup(
                user=user,
                finger=finger,
                force=force,
                skip_enroll=skip_enroll,
                interactive=not non_interactive,
                simulate=simulate,
            )
            color = "\033[1;32m" if ok else "\033[1;31m"
            print(f"\n{color}{msg}\033[0m")
            return 0 if ok else 1

        case "detect":
            readers = orchestrator.detector.detect_readers()
            if getattr(args, "json", False):
                print(json.dumps([r.to_dict() for r in readers], indent=2))
            else:
                if readers:
                    print(f"Found {len(readers)} fingerprint reader(s):")
                    for r in readers:
                        print(f"  • {r.product_name} [{r.vendor_id}:{r.product_id}] at {r.sysfs_path} ({r.matched_by})")
                else:
                    print("No fingerprint reader detected.")
            return 0 if readers else 1

        case "clamshell":
            status = orchestrator.clamshell.check()
            if not getattr(args, "quiet", False):
                if getattr(args, "json", False):
                    print(json.dumps(dataclasses.asdict(status), indent=2))
                else:
                    state_str = "CLOSED" if status.is_closed else "OPEN / ABSENT"
                    print(f"Lid State: {state_str} (method: {status.method_used}, detail: {status.detail})")
            return 0 if status.is_closed else 1

        case "status":
            report = orchestrator.get_system_status(user=user)
            if getattr(args, "json", False):
                print(json.dumps(dataclasses.asdict(report), indent=2))
            else:
                print("==================================================")
                print("       DUSKY FINGERPRINT SYSTEM STATUS            ")
                print("==================================================")
                print(f"Hardware Detected:     {'Yes' if report.hardware_present else 'No'}")
                if report.devices:
                    for d in report.devices:
                        print(f"  └─ {d['product_name']} [{d['vendor_id']}:{d['product_id']}]")
                print(f"Packages Installed:    fprintd={report.packages_installed['fprintd']}, libfprint-git={report.packages_installed['libfprint-git']}, libfprint={report.packages_installed['libfprint']}")
                print(f"fprintd Active:        {'Yes' if report.fprintd_service_active else 'No'}")
                print(f"Sudo PAM Configured:   {'Yes' if report.sudo_pam_configured else 'No'}")
                print(f"Polkit PAM Configured: {'Yes' if report.polkit_pam_configured else 'No'}")
                print(f"Clamshell Gate Bin:    {'Yes' if report.clamshell_gate_installed else 'No'}")
                print(f"Resume Hook Installed: {'Yes' if report.resume_hook_installed else 'No'}")
                print(f"Hyprlock Configured:   {'Yes' if report.hyprlock_configured else 'No'}")
                print(f"Enrolled Fingers:      {', '.join(report.enrolled_fingers) or 'None'} (probe: {report.probe_classification})")
                print(f"Lid Status:            {'CLOSED' if report.lid_status['is_closed'] else 'OPEN'} ({report.lid_status['method_used']})")
                print("==================================================")
            return 0

        case "enroll":
            target_user = IdentityResolver.validate_username(user) if user else IdentityResolver.resolve_target_user()
            ok, msg = orchestrator.enrollment.enroll(target_user, finger=args.finger)
            color = "\033[1;32m" if ok else "\033[1;31m"
            print(f"{color}{msg}\033[0m")
            return 0 if ok else 1

        case "verify":
            target_user = IdentityResolver.validate_username(user) if user else IdentityResolver.resolve_target_user()
            ok, msg = orchestrator.enrollment.verify(target_user, finger=args.finger)
            color = "\033[1;32m" if ok else "\033[1;31m"
            print(f"{color}{msg}\033[0m")
            return 0 if ok else 1

        case "remove":
            ok, msg = orchestrator.run_removal(user=user, purge_packages=getattr(args, "purge", False))
            color = "\033[1;32m" if ok else "\033[1;31m"
            print(f"{color}{msg}\033[0m")
            return 0 if ok else 1

        case "should-invite":
            should = InvitationManager.should_invite(orchestrator, user=user)
            if should:
                print("Invitation recommended: Fingerprint reader detected and unconfigured.")
                return 0
            else:
                print("No invitation needed.")
                return 1

        case "invite":
            sent = InvitationManager.send_invitation(orchestrator, user=user, force=getattr(args, "force", False))
            if sent:
                print("\033[1;32m[✓]\033[0m Invitation notification dispatched.")
                return 0
            else:
                print("Invitation not sent (hardware not present, already configured, or notification suppressed).")
                return 1

        case "install-gate":
            ok, msg = orchestrator.install_clamshell_gate()
            color = "\033[1;32m" if ok else "\033[1;31m"
            print(f"{color}{msg}\033[0m")
            return 0 if ok else 1

        case "test":
            test_file = Path(__file__).parent / "test_setup_fingerprint.py"
            if not test_file.is_file():
                print(f"\033[1;31m[-] Test file not found: {test_file}\033[0m", file=sys.stderr)
                return 1
            cmd = [sys.executable, str(test_file)]
            if getattr(args, "verbose", False):
                cmd.append("-v")
            res = subprocess.run(cmd)
            return res.returncode

        case _:
            parser.print_help()
            return 1


if __name__ == "__main__":
    sys.exit(main())
