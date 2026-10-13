#!/usr/bin/env python3
#d: Optimize the initramfs with extra hooks

"""Optimize the installed system's mkinitcpio drop-in, then rebuild presets."""

import argparse
import json
import os
import shlex
import shutil
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
    sys.exit("[ERR] Install the required library with: sudo pacman -S python-rich")

console = Console()
CONFIG = Path("/etc/mkinitcpio.conf")
TARGET = Path("/etc/mkinitcpio.conf.d/10-arch-btrfs-luks.conf")
PRESETS = Path("/etc/mkinitcpio.d")
BEGIN = "# BEGIN 295_initramfs_optimization.py\n"
END = "# END 295_initramfs_optimization.py\n"
ARRAYS = ("MODULES", "BINARIES", "HOOKS")


def capture(argv: list[str], *, input: str | None = None) -> str:
    """Keep command diagnostics visible and propagate failures to the caller."""
    return subprocess.run(
        argv, input=input, check=True, stdout=subprocess.PIPE,
        text=True, encoding="utf-8",
        env={k: v for k, v in os.environ.items() if k != "BASH_ENV"}
        | {"LC_ALL": "C.UTF-8"},
    ).stdout


def read_arrays(content: str) -> dict[str, list[str]]:
    """Use Bash itself for quoted, multiline, appended and scalar settings.

    mkinitcpio concatenates its configuration before sourcing it. These are
    executable system configuration files, not a data format for a regex parser.
    """
    program = r'''
unset MODULES BINARIES HOOKS
source /dev/stdin >&2 || exit
for name in MODULES BINARIES HOOKS; do
    declare -n values="$name"
    if [[ ${values@a} != *a* ]]; then
        IFS=' ' read -r -a values <<< "$values"
    fi
    printf '%s\0' "${#values[@]}"
    if (( ${#values[@]} )); then printf '%s\0' "${values[@]}"; fi
done
'''
    fields = iter(capture(["bash", "--noprofile", "--norc", "-c", program], input=content).split("\0"))
    try:
        result = {name: [next(fields) for _ in range(int(next(fields)))] for name in ARRAYS}
        if list(fields) != [""]:
            raise ValueError("unexpected output")
        return result
    except (ValueError, StopIteration) as error:
        raise RuntimeError("Cannot read mkinitcpio arrays from Bash output") from error


def config_files() -> list[Path]:
    # Match mkinitcpio's sort -V ordering (plain lexical sorting is different).
    names = [p.name for p in TARGET.parent.glob("*.conf") if p.is_file()]
    if TARGET.name not in names:
        names.append(TARGET.name)
    ordered = capture(["sort", "--zero-terminated", "--version-sort", "--unique"], input="\0".join(names) + "\0")
    files = [TARGET.parent / name for name in ordered.split("\0") if name]
    if TARGET not in files:
        raise RuntimeError("A drop-in with an equivalent version-sort name shadows the managed file")
    return [CONFIG, *files]


def combined_config(files: list[Path], target_content: str) -> str:
    return "".join(target_content if path == TARGET else path.read_text(encoding="utf-8") for path in files)


def check_presets() -> None:
    presets = sorted(PRESETS.glob("*.preset"))
    if not presets:
        raise RuntimeError("No mkinitcpio presets found")
    program = r'''
source "$1" >&2 || exit
buildable=0
for preset in "${PRESETS[@]}"; do
    name="${preset}_kver"
    [[ -n ${!name:-${ALL_kver:-}} ]] || continue
    name="${preset}_image"
    image="${!name:-}"
    name="${preset}_uki"
    uki="${!name:-}"
    [[ -n $image || -n $uki ]] || continue
    name="${preset}_config"
    config="${!name:-${ALL_config:-}}"
    if [[ -n $config ]]; then
        printf 'Preset %s in %s uses %s and bypasses drop-ins.\n' "$preset" "$1" "$config" >&2
        exit 1
    fi
    name="${preset}_options"
    declare -n options="$name"
    if [[ ${options@a} == *a* ]]; then
        arguments=("${options[@]}")
    else
        mapfile -d ' ' -t arguments < <(printf '%s' "$options")
    fi
    for option in "${arguments[@]}"; do
        case "$option" in
            --?*)
                # mkinitcpio accepts unambiguous long-option prefixes.
                name=${option%%=*}
                name=${name#--}
                for action in config help version listhooks hookhelp automods remove allpresets preset; do
                    if [[ $action == "$name"* ]]; then
                        printf 'Preset %s in %s bypasses the default configuration or image rebuild.\n' "$preset" "$1" >&2
                        exit 1
                    fi
                done ;;
            -[!-]*)
                # Short options may be clustered; stop at an option's argument
                # so e.g. the c in -zcat is not mistaken for --config.
                for (( i = 1; i < ${#option}; ++i )); do
                    flag=${option:i:1}
                    case "$flag" in
                        c|h|V|L|H|M|R|P|p)
                            printf 'Preset %s in %s bypasses the default configuration or image rebuild.\n' "$preset" "$1" >&2
                            exit 1 ;;
                        A|D|I|g|k|r|S|d|t|U|z) break ;;
                    esac
                done ;;
        esac
    done
    unset -n options
    (( ++buildable ))
done
printf '%s\n' "$buildable"
'''
    count = sum(int(capture([
        "bash", "--noprofile", "--norc", "-c", program, "bash", str(preset)
    ])) for preset in presets)
    if not count:
        raise RuntimeError("No enabled mkinitcpio preset has both a kernel and an image/UKI output")


def detect_topology() -> tuple[bool, set[str]]:
    mounts = json.loads(capture([
        "findmnt", "--json", "--nofsroot", "--evaluate", "--target", "/",
        "--output", "SOURCE,FSTYPE,UUID",
    ]))["filesystems"]
    if len(mounts) != 1:
        raise RuntimeError("Cannot identify a single installed root filesystem")
    root = mounts[0]
    is_btrfs = root["fstype"] == "btrfs"
    devices = [Path(root["source"])]
    if is_btrfs:
        # Trace every member of a multi-device Btrfs root, including mixed crypt layers.
        members = Path("/sys/fs/btrfs") / root["uuid"] / "devices"
        devices = [Path("/dev/block") / (p / "dev").read_text(encoding="utf-8").strip()
                   for p in members.iterdir()]
    if not devices or any(not device.is_block_device() for device in devices):
        raise RuntimeError("Root is not backed by accessible block devices; run on the installed system")
    types = set(capture([
        "lsblk", "--inverse", "--list", "--noheadings", "--output", "TYPE",
        "--", *(str(device) for device in devices),
    ]).split())
    if not types:
        raise RuntimeError("lsblk returned no root device topology")
    return is_btrfs, types


def optimize_arrays(existing: dict[str, list[str]], is_btrfs: bool,
                    types: set[str]) -> dict[str, list[str]]:
    hooks = existing["HOOKS"]
    legacy = {"udev", "encrypt", "plymouth-encrypt", "keymap", "consolefont", "resume", "usr"} & set(hooks)
    if legacy:
        raise RuntimeError(
            f"Migrate legacy hooks to systemd first: {', '.join(sorted(legacy))}. "
            "Encryption migration may also require kernel command-line changes."
        )
    prefix = ["base", "systemd"]
    if "plymouth" in hooks:
        prefix.append("plymouth")
    # Include all keyboard drivers so external keyboards can unlock an encrypted root.
    prefix += ["keyboard", "autodetect", "microcode", "modconf", "kms", "sd-vconsole", "block"]
    # Keep custom hooks in their existing positions. Only move hooks with a
    # specific ordering requirement; custom hooks may intentionally precede
    # autodetect or follow filesystems.
    hooks = list(dict.fromkeys(hooks))
    if "filesystems" not in hooks:
        hooks.append("filesystems")
    anchor = "filesystems"
    for hook in reversed(prefix):
        if hook not in hooks:
            hooks.insert(hooks.index(anchor), hook)
        anchor = hook
    for hook in reversed(prefix[:3] if "plymouth" in prefix else prefix[:2]):
        hooks.remove(hook)
        hooks.insert(0, hook)
    hooks.remove("keyboard")
    hooks.insert(hooks.index("autodetect"), "keyboard")
    for needed, present in (("sd-encrypt", "crypt" in types),
                            ("lvm2", "lvm" in types),
                            ("mdadm_udev", any(t.startswith("raid") or t == "md" for t in types))):
        if present and needed not in hooks:
            hooks.insert(hooks.index("filesystems"), needed)
    if "sd-encrypt" in hooks and hooks.index("sd-encrypt") < hooks.index("block"):
        hooks.remove("sd-encrypt")
        hooks.insert(hooks.index("block") + 1, "sd-encrypt")
    if not is_btrfs and "fsck" not in hooks:
        hooks.insert(hooks.index("filesystems") + 1, "fsck")
    modules = existing["MODULES"].copy()
    binaries = existing["BINARIES"].copy()
    if is_btrfs:
        if "btrfs" not in modules:
            modules.append("btrfs")
        binary = shutil.which("btrfs")
        if binary is None:
            raise RuntimeError("A Btrfs root requires btrfs-progs")
        if binary not in binaries:
            binaries.append(binary)
    return {"MODULES": modules, "BINARIES": binaries,
            "HOOKS": hooks}


def render_config(original: str, arrays: dict[str, list[str]]) -> str:
    # Keep FILES, compression settings and all other user configuration intact.
    if (original.count(BEGIN) != original.count(END) or original.count(BEGIN) > 1
            or BEGIN in original and original.index(END) < original.index(BEGIN)):
        raise RuntimeError("Malformed managed configuration block; repair its markers first")
    if BEGIN in original:
        start = original.index(BEGIN)
        finish = original.index(END, start) + len(END)
        original = original[:start] + original[finish:]
    block = "".join(f"{name}=({shlex.join(arrays[name])})\n" for name in ARRAYS)
    return original.rstrip("\n") + "\n" + BEGIN + block + END


def sync_directory(directory: Path) -> None:
    """Persist both configuration replacements and rollback removals."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path, content: bytes) -> None:
    """Replace a complete file and retain its existing ownership and mode."""
    path = path.resolve()  # Preserve a configured symlink rather than replace it.
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = path.stat() if path.exists() else None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            if metadata is not None:
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode) if metadata else 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def deploy(payload: str) -> None:
    # Resolve once so rollback of a newly created symlink target removes the
    # actual file, leaving the original symlink intact.
    target = TARGET.resolve()
    original = target.read_bytes() if target.exists() else None
    try:
        atomic_write(target, payload.encode("utf-8"))
        subprocess.run(["mkinitcpio", "--allpresets"], check=True)
    except (OSError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        try:
            if original is None:
                if target.exists():
                    target.unlink()
                    sync_directory(target.parent)
            else:
                atomic_write(target, original)
        except OSError as rollback_error:
            raise RuntimeError(
                f"Update failed ({error}); configuration rollback also failed ({rollback_error}). "
                "Repair the configuration and rebuild before rebooting."
            ) from rollback_error
        console.print("[bold red][ERR][/bold red] Configuration restored. A failed build may have already "
                      "changed some boot images; rebuild successfully before rebooting.")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--auto", action="store_true", help="Apply without interactive prompts.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and print configuration without writing or rebuilding.")
    args = parser.parse_args()
    for variable, expected in (("MKINITCPIO_CONF", CONFIG), ("MKINITCPIO_PRESETS", PRESETS)):
        if os.environ.get(variable, str(expected)) != str(expected):
            raise RuntimeError(f"Unset {variable} before optimizing the default system configuration")
    if not args.dry_run and os.geteuid() != 0:
        console.print("[blue][INFO][/blue] Root privileges required. Elevating...")
        os.execvp("sudo", ["sudo", "--", sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])
    console.print(Panel.fit("Arch Linux Initramfs Optimizer", border_style="cyan"))
    check_presets()
    is_btrfs, types = detect_topology()
    files = config_files()
    original = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    arrays = optimize_arrays(read_arrays(combined_config(files, original)), is_btrfs, types)
    payload = render_config(original, arrays)
    if read_arrays(combined_config(files, payload)) != arrays:
        raise RuntimeError("Another drop-in overrides the proposed arrays; resolve its precedence first")
    hook_dirs = [Path(directory) for directory in os.environ.get(
        "MKINITCPIO_INSTALL", "/etc/initcpio/install:/usr/lib/initcpio/install"
    ).split(":")]
    missing = [hook for hook in arrays["HOOKS"] if not any(
        (directory / hook).is_file()
        for directory in hook_dirs
    )]
    if missing:
        raise RuntimeError(f"Missing mkinitcpio install hooks: {', '.join(missing)}")
    console.print(f"Btrfs root: {is_btrfs}; encrypted root: {'crypt' in types}")
    console.print(payload, markup=False, highlight=False)
    if args.dry_run:
        return 0
    if not args.auto and not Confirm.ask("Apply this configuration and rebuild all initramfs presets?"):
        return 0
    deploy(payload)
    console.print("[bold green][OK][/bold green] Initramfs regeneration complete; changes apply on next reboot.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        console.print("[red][ERR][/red] Interrupted.")
        sys.exit(130)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.CalledProcessError) as error:
        console.print(f"[ERR] {error}", style="red", markup=False)
        sys.exit(1)
