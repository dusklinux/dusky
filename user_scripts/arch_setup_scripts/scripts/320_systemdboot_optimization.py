#!/usr/bin/env python3
#d: Optimize systemd-boot

"""Optimize Type #1 Linux directives; preserve fallback and other entries."""

import argparse
import difflib
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
except ImportError:
    print("[ERR] The 'rich' library is required. Install it with: sudo pacman -S python-rich", file=sys.stderr)
    sys.exit(1)

console = Console()
EFI_TIMEOUT = Path("/sys/firmware/efi/efivars/LoaderConfigTimeout-4a67b082-0a4c-41cf-b6c7-440b29bb8c4f")


def bootctl(*arguments: str) -> str:
    """Run the documented systemd interface without a pager."""
    result = subprocess.run(
        ["bootctl", "--no-pager", *arguments],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return result.stdout.strip()


def atomic_write(file_path: Path, content: str) -> None:
    """Replace a file on its own filesystem and sync both data and rename."""
    metadata = file_path.stat() if file_path.exists() else None
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, encoding="utf-8", newline="\n",
            dir=file_path.parent, prefix=".boot-optimizer-",
        ) as stream:
            temporary = Path(stream.name)
            current = os.fstat(stream.fileno())
            if metadata and (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            mode = stat.S_IMODE(metadata.st_mode) if metadata else 0o644
            # FAT exposes mount-wide permissions; normally these already match.
            if stat.S_IMODE(current.st_mode) != mode:
                os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, file_path)
        directory_fd = os.open(file_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def optimize_loader(content: str) -> str:
    """Keep one exact timeout directive, preserving all unrelated lines."""
    output: list[str] = []
    found = False
    for line in content.splitlines(keepends=True):
        if re.prefixmatch(r"[ \t]*timeout(?:[ \t]|\r?$)", line):
            if not found:
                output.append("timeout 0\n")
                found = True
        else:
            output.append(line)
    if not found:
        if output and not output[-1].endswith("\n"):
            output.append("\n")
        output.append("timeout 0\n")
    return "".join(output)


def directive_value(line: str, key: str) -> str | None:
    """Apply systemd-boot's whitespace and whole-value unquoting rules."""
    match = re.prefixmatch(rf"[ \t]*{key}[ \t]+([^\r\n]*)", line)
    if match is None:
        return None
    value = match[1].strip(" \t")
    if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
        value = value[1:-1]
    return value


def optimize_entry(content: str, parameters: dict[str, str]) -> str:
    """Combine options as systemd does, then enforce exact kernel keys once."""
    if not parameters:
        return content
    lines = content.splitlines(keepends=True)
    option_lines: list[int] = []
    values: list[str] = []
    for index, line in enumerate(lines):
        value = directive_value(line, "options")
        if value is not None:
            option_lines.append(index)
            values.append(value)
    value = " ".join(values)
    # The kernel honors double quotes only, with no shell escaping. Quotes may
    # span options directives, since systemd joins their values before booting.
    if value.count('"') % 2:
        raise ValueError("Unbalanced double quotes in kernel options")
    parts = re.findall(r'\s+|(?:[^\s"]|"[^"]*")+', value)
    seen: set[str] = set()
    boundary = len(parts)
    for position, token in enumerate(parts):
        if token.isspace():
            continue
        unquoted = token[1:-1] if token.startswith('"') and token.endswith('"') else token
        if unquoted == "--":
            boundary = position
            break
        key = unquoted.partition("=")[0].replace("-", "_")
        if key == "slub_debug":
            key = "slab_debug"  # Migrate the kernel's legacy alias too.
        if key in parameters:
            parts[position] = "" if key in seen else parameters[key]
            seen.add(key)
    missing = " ".join(value for key, value in parameters.items() if key not in seen)
    if missing:
        before = "".join(parts[:boundary])
        separator = " " if before and not before[-1].isspace() else ""
        parts.insert(boundary, separator + missing + (" " if boundary < len(parts) else ""))
    updated = "".join(parts)
    # Avoid an additional whole-value unquoting pass changing kernel quotes.
    # systemd trims outer spaces before checking quotes, including spaces left
    # behind when trailing duplicate parameters were removed.
    stripped = updated.strip(" \t")
    if len(stripped) >= 2 and stripped[0] in "\"'" and stripped[-1] == stripped[0]:
        quote = '"' if stripped[0] == "'" else "'"
        updated = quote + updated + quote
    if not option_lines:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"options {updated}\n")
    elif len(option_lines) > 1 or updated != values[0]:
        first = option_lines[0]
        prefix = re.prefixmatch(r"[ \t]*options[ \t]+", lines[first])[0]
        ending = "\r\n" if lines[first].endswith("\r\n") else "\n" if lines[first].endswith("\n") else ""
        lines[first] = prefix + updated + ending
        for index in option_lines[1:]:
            lines[index] = ""
    return "".join(lines)


def select_parameters(auto: bool, has_entries: bool) -> tuple[bool, dict[str, str]]:
    timeout = auto or Confirm.ask("Hide boot menu by default? (timeout 0)")
    parameters: dict[str, str] = {}
    if has_entries:
        if auto or Confirm.ask("Reduce kernel boot messages? (quiet)"):
            parameters["quiet"] = "quiet"
        if auto or Confirm.ask("Disable zswap to favor zram? (zswap.enabled=0)"):
            parameters["zswap.enabled"] = "zswap.enabled=0"
        if not auto and Confirm.ask("Force PCIe ASPM? May cause lockups on unsupported hardware", default=False):
            parameters["pcie_aspm"] = "pcie_aspm=force"
        if auto or Confirm.ask("Disable IPv6 networking? (ipv6.disable=1)", default=True):
            parameters["ipv6.disable"] = "ipv6.disable=1"
        if auto or Confirm.ask("Disable slab debugging and allocation/free zeroing?", default=True):
            parameters.update({
                "slab_debug": "slab_debug=-",
                "init_on_alloc": "init_on_alloc=0",
                "init_on_free": "init_on_free=0",
            })
    return timeout, parameters


def main() -> int:
    parser = argparse.ArgumentParser(description="Systemd-boot Optimizer", allow_abbrev=False)
    parser.add_argument("--auto", action="store_true", help="Use the ISO defaults without prompts; never force ASPM.")
    parser.add_argument("--dry-run", action="store_true", help="Preview file changes without writing files or EFI variables.")
    args = parser.parse_args()
    if os.geteuid() != 0:
        console.print("[bold blue][INFO][/bold blue] Root privileges required. Elevating...")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])

    installed = subprocess.run(
        ["bootctl", "--no-pager", "is-installed"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if installed.returncode == 1 and installed.stdout.strip() == "no":
        console.print("[yellow]Systemd-boot is not installed; skipping.[/yellow]")
        return 0
    if installed.returncode != 0:
        raise RuntimeError(installed.stderr.strip() or "Could not determine whether systemd-boot is installed")
    esp = Path(bootctl("--print-esp-path"))
    boot = Path(bootctl("--print-boot-path"))
    if not esp.is_absolute() or not boot.is_absolute():
        raise ValueError("bootctl returned an invalid partition path")
    loader_conf = esp / "loader/loader.conf"
    console.print(Panel.fit("[bold]Arch Linux Systemd-boot Optimizer[/bold]", border_style="cyan"))

    # Read every candidate before committing anything. Entries using EFI/UKI
    # directives need a different workflow; only Linux directives are eligible.
    entries: dict[Path, str] = {}
    for root in dict.fromkeys((esp, boot)):
        directory = root / "loader/entries"
        if not directory.exists():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.suffix.lower() != ".conf" or "-fallback" in entry.name.lower() or not entry.is_file():
                continue
            content = entry.read_text(encoding="utf-8", newline="")
            if any(directive_value(line, "linux") for line in content.splitlines()):
                entries[entry] = content
            else:
                console.print(f"Skipped entry without a Linux directive: {entry}", markup=False, highlight=False)
    timeout, parameters = select_parameters(args.auto, bool(entries))
    if not entries:
        console.print("[yellow]No conventional Linux entries found; kernel parameters were not changed.[/yellow]")

    changes: list[tuple[Path, str, str]] = []
    if timeout:
        original = loader_conf.read_text(encoding="utf-8", newline="") if loader_conf.exists() else ""
        updated = optimize_loader(original)
        if original != updated:
            changes.append((loader_conf, original, updated))
    for entry, original in entries.items():
        try:
            updated = optimize_entry(original, parameters)
        except ValueError as error:
            raise ValueError(f"{entry}: {error}") from error
        if original != updated:
            changes.append((entry, original, updated))

    for path, original, updated in changes:
        if args.dry_run:
            console.print("".join(difflib.unified_diff(
                original.splitlines(keepends=True), updated.splitlines(keepends=True),
                fromfile=str(path), tofile=str(path),
            )), markup=False, highlight=False, end="")
        else:
            atomic_write(path, updated)
            console.print(f"Updated {path}", markup=False, highlight=False)
    # Menu changes saved by systemd-boot override loader.conf. Remove only an
    # existing persistent override, avoiding needless firmware writes.
    if timeout and EFI_TIMEOUT.exists():
        if args.dry_run:
            console.print("Would clear the persistent EFI boot-menu timeout override.")
        else:
            bootctl("set-timeout", "")
            console.print("Cleared the persistent EFI boot-menu timeout override.")
    if args.dry_run:
        console.print(f"Dry run complete: {len(changes)} file(s) would change.")
    else:
        console.print(f"Complete: {len(changes)} file(s) changed. Boot changes take effect on reboot.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("\n[red]Interrupted; earlier completed file changes remain in place.[/red]")
        sys.exit(130)
    except (OSError, ValueError, RuntimeError, EOFError, subprocess.CalledProcessError) as error:
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        detail = detail or type(error).__name__
        console.print(f"[ERR] {detail}. Earlier completed changes, if any, remain in place.", markup=False)
        sys.exit(1)
