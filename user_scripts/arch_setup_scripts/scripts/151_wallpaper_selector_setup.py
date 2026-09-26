#!/usr/bin/env python3
"""Use the ISO package when installed; otherwise build for this CPU."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


GIB = 1024 ** 3
TARGET = "x86_64-unknown-linux-gnu"
SYSTEM_BINARY = Path("/usr/bin/dusky-wallpaper-selector")


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", file=sys.stderr if level == "ERR" else sys.stdout, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(project: Path) -> str:
    files = [project / "Cargo.toml", project / "Cargo.lock"]
    files.extend(sorted((project / "src").rglob("*.rs")))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(project)).encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256(path)))
    return digest.hexdigest()


def binary_runs(binary: Path) -> bool:
    try:
        with binary.open("rb") as stream:
            header = stream.read(20)
        # ELF64, little endian, EM_X86_64.
        if header[:7] != b"\x7fELF\x02\x01\x01" or header[18:20] != b"\x3e\x00":
            return False
        result = subprocess.run(
            [str(binary), "--help"], capture_output=True, text=True, timeout=10, check=False
        )
        return result.returncode == 0 and "wallpaper_selector" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def get_project_version(project: Path) -> str:
    cargo_toml = project / "Cargo.toml"
    if not cargo_toml.is_file():
        return "1.0.0"
    for line in cargo_toml.read_text().splitlines():
        line = line.strip()
        if line.startswith("version"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"').strip("'")
    return "1.0.0"


def parse_version(v: str | None) -> tuple[int, ...]:
    if not v:
        return (-1,)
    cleaned = v.lstrip("vV").strip()
    parts = []
    for part in cleaned.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (-1,)


def binary_version(binary: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        if result.returncode == 0:
            for token in result.stdout.strip().split():
                if any(c.isdigit() for c in token):
                    return token.lstrip("vV")
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_signature() -> str:
    """Invalidate a local native build if a home directory moves to another CPU."""
    text = Path("/proc/cpuinfo").read_text()
    first_cpu = text.split("\n\n", 1)[0]
    fields = ("vendor_id", "cpu family", "model", "stepping", "flags")
    identity = "\n".join(line for line in first_cpu.splitlines()
                         if line.partition(":")[0].strip() in fields)
    return hashlib.sha256(identity.encode()).hexdigest()


def native_binary_valid(project: Path, binary: Path, manifest_path: Path, expected_version: str) -> bool:
    try:
        b_ver = binary_version(binary)
        if not b_ver or parse_version(b_ver) != parse_version(expected_version):
            return False
        manifest = json.loads(manifest_path.read_text())
        return (
            manifest.get("version") == expected_version
            and manifest.get("target") == TARGET
            and manifest.get("target_cpu") == "native"
            and manifest.get("cpu_signature") == cpu_signature()
            and manifest.get("source_sha256") == source_digest(project)
            and manifest.get("binary_sha256") == sha256(binary)
            and binary_runs(binary)
        )
    except (OSError, ValueError):
        return False


def memory_bytes() -> tuple[int, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.strip().split()[0]) * 1024
    total, available = values["MemTotal"], values["MemAvailable"]
    # Respect container or systemd cgroup limits when the setup is run inside one.
    for limit_path, current_path in (
        (Path("/sys/fs/cgroup/memory.max"), Path("/sys/fs/cgroup/memory.current")),
        (Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"), Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")),
    ):
        try:
            limit = int(limit_path.read_text().strip())
            current = int(current_path.read_text().strip())
        except (OSError, ValueError):
            continue
        total = min(total, limit)
        available = min(available, max(0, limit - current))
        break
    return total, available


def filesystem_type(path: Path) -> str | None:
    real_path = str(path.resolve())
    best = (0, None)
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        if len(fields) < 5:
            continue
        mount = fields[4].replace("\\040", " ")
        if (real_path == mount or real_path.startswith(mount.rstrip("/") + "/")) and len(mount) > best[0]:
            best = (len(mount), after.split()[0])
    return best[1]


def build_base() -> tuple[Path, bool]:
    try:
        total, available = memory_bytes()
    except (OSError, KeyError, ValueError):
        total = available = 0
    if total >= 8 * GIB and available >= 8 * GIB:
        for path in (Path("/mnt/zram1"), Path("/tmp")):
            if not path.is_dir() or not os.access(path, os.W_OK):
                continue
            if filesystem_type(path) not in ("tmpfs", "ramfs"):
                continue
            stat = os.statvfs(path)
            if stat.f_bavail * stat.f_frsize >= 8 * GIB:
                log("INFO", f"Building in memory at {path}")
                return path, True
    log("INFO", "Building on disk; temporary build files will be removed")
    disk_base = Path.home() / ".cache/dusky/wallpaper_selector_builds"
    disk_base.mkdir(parents=True, exist_ok=True)
    return disk_base, False


def prepare_cargo_home(build_dir: Path) -> Path:
    """Give Cargo a writable home even when a system setup owns ~/.cargo."""
    cargo_home = build_dir / "cargo-home"
    cargo_home.mkdir()
    existing = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    for subdir in ("registry", "git"):
        source = existing / subdir
        destination = cargo_home / subdir
        if not source.is_dir():
            continue
        try:
            # copyfile creates user-owned, writable files instead of preserving
            # read-only permissions from a root-owned Cargo installation.
            shutil.copytree(source, destination, copy_function=shutil.copyfile)
        except (OSError, shutil.Error) as error:
            for root, _, _ in os.walk(destination):
                path = Path(root)
                path.chmod(path.stat().st_mode | stat.S_IWUSR)
            shutil.rmtree(destination, ignore_errors=True)
            log("WARN", f"Could not reuse Cargo {subdir} cache ({error}); Cargo will download it")
            continue
        for root, _, _ in os.walk(destination):
            path = Path(root)
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
    for filename in ("config.toml", "config", "credentials.toml", "credentials"):
        source = existing / filename
        if source.is_file():
            try:
                shutil.copyfile(source, cargo_home / filename)
            except OSError as error:
                log("WARN", f"Could not copy Cargo {filename}: {error}")
    return cargo_home


def build_native(project: Path, binary: Path, manifest_path: Path, version: str) -> bool:
    cargo = shutil.which("cargo")
    if not cargo:
        log("ERR", "No usable ISO package and Cargo is not installed")
        return False
    base, _ = build_base()
    with tempfile.TemporaryDirectory(prefix="dusky-wall-build-", dir=base) as temp:
        target_dir = Path(temp) / "target"
        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = str(target_dir)
        env["CARGO_HOME"] = str(prepare_cargo_home(Path(temp)))
        env["RUSTFLAGS"] = "-C target-cpu=native"
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
        env["CFLAGS"] = "-march=native -mtune=native -O2"
        env["CXXFLAGS"] = env["CFLAGS"]
        env["CPPFLAGS"] = ""
        log("INFO", f"Compiling native release binary for this CPU (v{version})")
        result = subprocess.run(
            [cargo, "build", "--release", "--locked", "--target", TARGET],
            cwd=project, env=env, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            log("ERR", f"Cargo build failed:\n{result.stderr[-12000:]}")
            return False
        built = target_dir / TARGET / "release" / "wallpaper_selector"
        if not binary_runs(built):
            log("ERR", "Built binary failed its executable smoke test")
            return False
        temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(built, temporary_binary)
            temporary_binary.chmod(0o755)
            os.replace(temporary_binary, binary)
            manifest = {
                "version": version,
                "target": TARGET,
                "target_cpu": "native",
                "cpu_signature": cpu_signature(),
                "source_sha256": source_digest(project),
                "binary_sha256": sha256(binary),
            }
            temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
            temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
            os.replace(temporary_manifest, manifest_path)
        except OSError as error:
            log("ERR", f"Could not install built binary: {error}")
            return False
        finally:
            temporary_binary.unlink(missing_ok=True)
    return True


def main(argv: list[str] | None = None) -> int:
    home = Path.home()
    project = home / "user_scripts/images/wallpaper_selector"
    project_ver = get_project_version(project)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", "-v", action="version",
        version=f"wallpaper_selector_setup {project_ver}",
        help="Show version information and exit",
    )
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument(
        "--build-cache", "--update-cache", action="store_true",
        help="Generate only missing or outdated previews (also the default)",
    )
    cache_group.add_argument(
        "--rebuild-cache", action="store_true",
        help="Force-regenerate every preview",
    )
    args = parser.parse_args(argv)

    install_dir = home / ".local/share/dusky/wallpaper_selector"
    binary = install_dir / "wallpaper_selector"
    manifest_path = install_dir / "binary_manifest.json"
    legacy_binary = project / "wallpaper_selector"
    legacy_manifest = project / "binary_manifest.json"
    local_bin = home / ".local/bin/wallpaper_selector"
    thumb_dir = home / ".cache/dusky_images/wallpaper_selector_rust/thumbs"
    settings_dir = home / ".config/dusky/settings/dusky_theme"

    if platform.machine() != "x86_64":
        log("ERR", "This selector package targets x86-64 only")
        return 1
    if not (project / "Cargo.toml").is_file():
        log("ERR", f"Selector source is missing from {project}")
        return 1

    for directory in (thumb_dir, settings_dir, install_dir, local_bin.parent):
        directory.mkdir(parents=True, exist_ok=True)

    system_ver = binary_version(SYSTEM_BINARY) if binary_runs(SYSTEM_BINARY) else None
    if system_ver and parse_version(system_ver) >= parse_version(project_ver):
        log("OK", f"Using ISO package binary at {SYSTEM_BINARY} (v{system_ver})")
        temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
        try:
            temporary_binary.symlink_to(SYSTEM_BINARY)
            os.replace(temporary_binary, binary)
        except OSError as error:
            temporary_binary.unlink(missing_ok=True)
            log("ERR", f"Could not link ISO package binary: {error}")
            return 1
        manifest_path.unlink(missing_ok=True)
    else:
        if SYSTEM_BINARY.is_file():
            log("INFO", f"System binary at {SYSTEM_BINARY} is outdated (v{system_ver or 'legacy'} < v{project_ver}); using native build")
        if native_binary_valid(project, binary, manifest_path, project_ver):
            log("OK", f"Using verified native build for this CPU (v{project_ver})")
        elif native_binary_valid(project, legacy_binary, legacy_manifest, project_ver):
            temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
            temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
            try:
                shutil.copy2(legacy_binary, temporary_binary)
                shutil.copy2(legacy_manifest, temporary_manifest)
                os.replace(temporary_binary, binary)
                os.replace(temporary_manifest, manifest_path)
            except OSError as error:
                log("ERR", f"Could not migrate the verified native build: {error}")
                return 1
            finally:
                temporary_binary.unlink(missing_ok=True)
                temporary_manifest.unlink(missing_ok=True)
            log("OK", f"Moved verified native build out of the Git checkout (v{project_ver})")
        elif not build_native(project, binary, manifest_path, project_ver):
            return 1
        else:
            log("OK", f"Installed newly built native binary (v{project_ver})")

    temporary_link = local_bin.with_name(f".{local_bin.name}.{os.getpid()}.tmp")
    try:
        temporary_link.symlink_to(binary)
        os.replace(temporary_link, local_bin)
    except OSError as error:
        temporary_link.unlink(missing_ok=True)
        log("ERR", f"Could not create launcher symlink: {error}")
        return 1

    for legacy_file in (legacy_binary, legacy_manifest):
        legacy_file.unlink(missing_ok=True)

    cache_mode = "--rebuild-cache" if args.rebuild_cache else "--build-cache"
    log("INFO", "Regenerating all previews" if args.rebuild_cache else "Generating missing or stale previews")
    result = subprocess.run([str(binary), cache_mode], check=False)
    if result.returncode != 0:
        log("ERR", "Thumbnail generation failed")
        return 1
    log("OK", "Wallpaper selector setup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
