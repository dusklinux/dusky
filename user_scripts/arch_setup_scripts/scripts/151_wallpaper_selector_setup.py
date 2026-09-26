#!/usr/bin/env python3
"""Use the ISO package when installed; otherwise build for this CPU."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


GIB = 1024 ** 3
TARGET = "x86_64-unknown-linux-gnu"
SYSTEM_BINARY = Path("/usr/bin/dusky-wallpaper-selector")


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 3600) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


BUILD_RECIPE = "native-v2-frozen-sparse"
FETCH_TIMEOUT_S = env_int("DUSKY_WALLPAPER_FETCH_TIMEOUT", 15, maximum=120)
BUILD_TIMEOUT_S = env_int("DUSKY_WALLPAPER_BUILD_TIMEOUT", 600, maximum=1800)
CACHE_TIMEOUT_S = env_int("DUSKY_WALLPAPER_CACHE_TIMEOUT", 120, maximum=900)


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", file=sys.stderr if level == "ERR" else sys.stdout, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(project: Path) -> str:
    files: list[Path] = [
        project / "Cargo.toml",
        project / "Cargo.lock",
    ]
    for relative in (
        "build.rs",
        "rust-toolchain",
        "rust-toolchain.toml",
        ".cargo/config",
        ".cargo/config.toml",
    ):
        path = project / relative
        if path.is_file():
            files.append(path)

    src = project / "src"
    if src.is_dir():
        files.extend(sorted(path for path in src.rglob("*") if path.is_file()))

    digest = hashlib.sha256()
    digest.update(BUILD_RECIPE.encode())
    digest.update(b"\0")
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
            stat_res = os.statvfs(path)
            if stat_res.f_bavail * stat_res.f_frsize >= 8 * GIB:
                log("INFO", f"Building in memory at {path}")
                return path, True
    log("INFO", "Building on disk; temporary build files will be removed")
    disk_base = Path.home() / ".cache/dusky/wallpaper_selector_builds"
    disk_base.mkdir(parents=True, exist_ok=True)
    return disk_base, False


def process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def terminate_process_group(process: subprocess.Popen, *, grace_seconds: float = 3.0) -> None:
    """Terminate the entire session/process group, not merely its leader."""
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            process.wait(timeout=0)
        except (subprocess.TimeoutExpired, OSError):
            pass
        return

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline and process_group_exists(pgid):
        time.sleep(0.05)

    if process_group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        process.wait(timeout=2)
    except (subprocess.TimeoutExpired, OSError):
        pass


def run_bounded(
    command: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> tuple[int, str, str, bool]:
    """Run with a hard wall-clock deadline and process-group cleanup."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError as error:
        return (
            127,
            "",
            f"Could not start {command[0]!r}: {error}",
            False,
        )

    try:
        if capture_output:
            stdout, stderr = process.communicate(timeout=timeout)
        else:
            process.wait(timeout=timeout)
            stdout = stderr = ""
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        stdout = stderr = ""
        if capture_output:
            try:
                stdout, stderr = process.communicate(timeout=0)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass
        return (
            process.returncode if process.returncode is not None else -signal.SIGKILL,
            stdout,
            stderr,
            True,
        )
    except BaseException:
        terminate_process_group(process)
        raise


def get_cargo_home(build_dir: Path) -> Path:
    """Return a writable CARGO_HOME, creating a fallback in build_dir only if needed."""
    existing = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    try:
        existing.mkdir(parents=True, exist_ok=True)
        test_file = existing / f".write_test_{os.getpid()}"
        test_file.touch()
        test_file.unlink()
        return existing
    except OSError:
        pass

    cargo_home = build_dir / "cargo-home"
    cargo_home.mkdir(parents=True, exist_ok=True)
    for filename in ("config.toml", "config", "credentials.toml", "credentials"):
        source = existing / filename
        if source.is_file():
            try:
                shutil.copyfile(source, cargo_home / filename)
            except OSError as error:
                log("WARN", f"Could not copy Cargo {filename}: {error}")
    return cargo_home


def fetch_dependencies(cargo: str, project: Path, env: dict[str, str]) -> bool:
    """Fast preflight: verify dependencies are cached offline or fetch them with a strict timeout."""
    # 1. Fast check if all dependencies are already cached offline
    offline_cmd = [cargo, "fetch", "--locked", "--offline", "--target", TARGET]
    rc, _, _, _ = run_bounded(offline_cmd, cwd=project, env=env, timeout=10, capture_output=True)
    if rc == 0:
        return True

    # 2. Not cached locally; fetch from crates.io with strict network timeouts
    log("INFO", "Missing dependencies; fetching from crates.io (fast preflight)...")
    fetch_env = env.copy()
    fetch_env["CARGO_NET_OFFLINE"] = "false"
    fetch_env["CARGO_HTTP_TIMEOUT"] = "7"
    fetch_env["CARGO_NET_RETRY"] = "1"
    fetch_env["CARGO_REGISTRIES_CRATES_IO_PROTOCOL"] = "sparse"
    fetch_env["RUSTUP_AUTO_INSTALL"] = "0"

    fetch_cmd = [cargo, "fetch", "--locked", "--target", TARGET]
    rc, _, stderr, timed_out = run_bounded(
        fetch_cmd,
        cwd=project,
        env=fetch_env,
        timeout=FETCH_TIMEOUT_S,
        capture_output=True,
    )
    if timed_out:
        log("WARN", f"Cargo dependency fetch timed out after {FETCH_TIMEOUT_S}s (crates.io unreachable)")
        return False
    if rc != 0:
        log("WARN", f"Cargo dependency fetch failed (exit {rc}):\n{stderr[-1000:].strip()}")
        return False
    return True


def link_system_binary(system_binary: Path, binary: Path) -> bool:
    temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
    try:
        temporary_binary.symlink_to(system_binary)
        os.replace(temporary_binary, binary)
        return True
    except OSError as error:
        log("WARN", f"Could not link system package binary: {error}")
        return False
    finally:
        temporary_binary.unlink(missing_ok=True)


def build_native(project: Path, binary: Path, manifest_path: Path, version: str) -> bool:
    cargo = shutil.which("cargo")
    if not cargo:
        log("WARN", "Cargo is not installed; skipping native compilation")
        return False

    base, _ = build_base()
    with tempfile.TemporaryDirectory(prefix="dusky-wall-build-", dir=base) as temp:
        temp_dir = Path(temp)
        target_dir = temp_dir / "target"
        cargo_home = get_cargo_home(temp_dir)

        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = str(target_dir)
        env["CARGO_HOME"] = str(cargo_home)
        env["CARGO_HTTP_TIMEOUT"] = "15"
        env["CARGO_NET_RETRY"] = "1"
        env["CARGO_REGISTRIES_CRATES_IO_PROTOCOL"] = "sparse"
        env["RUSTUP_AUTO_INSTALL"] = "0"
        env["RUSTFLAGS"] = "-C target-cpu=native"
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
        env["CFLAGS"] = "-march=native -mtune=native -O2"
        env["CXXFLAGS"] = env["CFLAGS"]
        env["CPPFLAGS"] = ""

        # Preflight: ensure dependencies are available before starting compilation
        if not fetch_dependencies(cargo, project, env):
            return False

        build_env = env.copy()
        build_env["CARGO_NET_OFFLINE"] = "true"
        build_env["RUSTUP_AUTO_INSTALL"] = "0"

        log("INFO", f"Compiling native release binary for this CPU (v{version})")
        command = [cargo, "build", "--release", "--frozen", "--target", TARGET]
        returncode, _, stderr, timed_out = run_bounded(
            command,
            cwd=project,
            env=build_env,
            timeout=BUILD_TIMEOUT_S,
            capture_output=True,
        )
        if timed_out:
            log("WARN", f"Cargo build exceeded {BUILD_TIMEOUT_S}s and was terminated")
            return False
        if returncode != 0:
            log("WARN", f"Cargo build failed:\n{stderr[-12000:]}")
            return False

        built = target_dir / TARGET / "release" / "wallpaper_selector"
        if not binary_runs(built):
            log("WARN", "Built binary failed its executable smoke test")
            return False

        temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
        temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
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
            temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
            os.replace(temporary_manifest, manifest_path)
        except OSError as error:
            log("WARN", f"Could not install built binary: {error}")
            return False
        finally:
            temporary_binary.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
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
        log("WARN", "This selector package targets x86-64 only; skipping setup")
        return 0
    if not (project / "Cargo.toml").is_file():
        log("WARN", f"Selector source is missing from {project}; skipping setup")
        return 0

    for directory in (thumb_dir, settings_dir, install_dir, local_bin.parent):
        directory.mkdir(parents=True, exist_ok=True)

    system_ok = binary_runs(SYSTEM_BINARY)
    system_ver = binary_version(SYSTEM_BINARY) if system_ok else None

    selected = False

    # 1. Use system package binary if it's already installed and at least as new as source
    if system_ver and parse_version(system_ver) >= parse_version(project_ver):
        log("OK", f"Using ISO package binary at {SYSTEM_BINARY} (v{system_ver})")
        if link_system_binary(SYSTEM_BINARY, binary):
            manifest_path.unlink(missing_ok=True)
            selected = True

    # 2. Use existing verified native build if source, CPU, version, and hashes match
    if not selected and native_binary_valid(project, binary, manifest_path, project_ver):
        log("OK", f"Using verified native build for this CPU (v{project_ver})")
        selected = True

    # 3. Migrate verified native build from project dir if found
    if not selected and native_binary_valid(project, legacy_binary, legacy_manifest, project_ver):
        temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
        temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(legacy_binary, temporary_binary)
            shutil.copy2(legacy_manifest, temporary_manifest)
            os.replace(temporary_binary, binary)
            os.replace(temporary_manifest, manifest_path)
            log("OK", f"Moved verified native build out of the Git checkout (v{project_ver})")
            selected = True
        except OSError as error:
            log("WARN", f"Could not migrate the verified native build: {error}")
        finally:
            temporary_binary.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)

    # 4. Attempt native compilation
    if not selected:
        if system_ok:
            log("INFO", f"System binary at {SYSTEM_BINARY} is outdated (v{system_ver or 'legacy'} < v{project_ver}); attempting native build")
        built_ok = build_native(project, binary, manifest_path, project_ver)
        if built_ok:
            log("OK", f"Installed newly built native binary (v{project_ver})")
            selected = True

    # 5. Fallback if build was unavailable or failed
    if not selected:
        current_local_ok = binary_runs(binary)
        current_local_ver = binary_version(binary) if current_local_ok else None

        if system_ok:
            log("WARN", f"Native build unavailable; attempting packaged binary v{system_ver or 'unknown'}")
            if link_system_binary(SYSTEM_BINARY, binary):
                manifest_path.unlink(missing_ok=True)
                selected = True
            else:
                log("WARN", "Could not link packaged fallback binary")

        if not selected and current_local_ok:
            log("WARN", f"Native build unavailable; keeping previously installed binary v{current_local_ver or 'unknown'}")
            selected = True

        if not selected:
            log("WARN", "Wallpaper selector binary build skipped or failed, and no fallback binary is available; continuing setup")
            return 0

    if binary_runs(binary):
        temporary_link = local_bin.with_name(f".{local_bin.name}.{os.getpid()}.tmp")
        try:
            temporary_link.symlink_to(binary)
            os.replace(temporary_link, local_bin)
        except OSError as error:
            temporary_link.unlink(missing_ok=True)
            log("WARN", f"Could not create launcher symlink: {error}")
            return 0
    else:
        log("WARN", "No runnable wallpaper selector binary available; skipping launcher symlink")
        return 0

    for legacy_file in (legacy_binary, legacy_manifest):
        legacy_file.unlink(missing_ok=True)

    cache_mode = "--rebuild-cache" if args.rebuild_cache else "--build-cache"
    log("INFO", "Regenerating all previews" if args.rebuild_cache else "Generating missing or stale previews")
    returncode, _, _, timed_out = run_bounded(
        [str(binary), cache_mode],
        timeout=CACHE_TIMEOUT_S,
        capture_output=False,
    )
    if timed_out:
        log("WARN", f"Thumbnail generation exceeded {CACHE_TIMEOUT_S}s and was terminated; continuing setup")
        return 0
    if returncode != 0:
        log("WARN", f"Thumbnail generation failed (exit {returncode}); continuing setup")
        return 0
    log("OK", "Wallpaper selector setup complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
