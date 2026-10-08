#!/usr/bin/env python3
"""Install the matching ISO binary, or compile Dusky Center for this CPU; never launch it."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

GIB = 1024 ** 3
TARGET = "x86_64-unknown-linux-gnu"
SYSTEM_BINARY = Path("/usr/bin/dusky-center")
SYSTEM_SOURCE_DIGEST = Path("/usr/share/dusky-center/source.sha256")
SYSTEM_DEFAULTS = Path("/usr/share/dusky-center/dusky_config.toml")
BUILD_RECIPE = "center-native-v1-frozen-tmpfs"


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 3600) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


FETCH_TIMEOUT_S = env_int("DUSKY_CENTER_FETCH_TIMEOUT", 120, maximum=1800)
BUILD_TIMEOUT_S = env_int("DUSKY_CENTER_BUILD_TIMEOUT", 7200, maximum=7200)


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
            [str(binary), "--help"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0 and "dusky-center" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def get_project_version(project: Path) -> str:
    cargo_toml = project / "Cargo.toml"
    with cargo_toml.open("rb") as stream:
        return tomllib.load(stream)["package"]["version"]


def binary_version(binary: Path) -> str | None:
    try:
        result = subprocess.run(
            [str(binary), "--version"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
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
    text = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    first_cpu = text.split("\n\n", 1)[0]
    fields = ("vendor_id", "cpu family", "model", "stepping", "flags")
    identity = "\n".join(line for line in first_cpu.splitlines()
                         if line.partition(":")[0].strip() in fields)
    return hashlib.sha256(identity.encode()).hexdigest()


def memory_bytes() -> tuple[int, int]:
    values = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            values[key] = int(value.strip().split()[0]) * 1024
    total, available = values["MemTotal"], values["MemAvailable"]
    cgroup = next((line.partition("::")[2] for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
                   if line.startswith("0::")), "/")
    root = Path("/sys/fs/cgroup")
    group = root / cgroup.lstrip("/")
    for directory in (group, *group.parents):
        if not directory.is_relative_to(root):
            break
        try:
            limit = int((directory / "memory.max").read_text(encoding="utf-8").strip())
            current = int((directory / "memory.current").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        total = min(total, limit)
        available = min(available, max(0, limit - current))
    return total, available


def filesystem_type(path: Path) -> str | None:
    real_path = str(path.resolve())
    best = (0, None)
    for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
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
        process.poll()
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


def build_base() -> Path:
    """Never fall back to disk, including on machines with limited RAM."""
    for path in (Path("/tmp"), Path("/dev/shm")):
        if not path.is_dir() or not os.access(path, os.W_OK):
            continue
        if filesystem_type(path) != "tmpfs":
            continue
        info = os.statvfs(path)
        if not info.f_flag & os.ST_NOEXEC and info.f_bavail * info.f_frsize >= 3 * GIB:
            return path
    raise OSError("Native builds need an executable tmpfs with at least 3 GiB free")


def run_bounded(command: list[str], *, timeout: int, cwd: Path | None = None,
                env: dict[str, str] | None = None, capture_output: bool = False
                ) -> tuple[int, str, str, bool]:
    """Bound fetch/build time and clean up the complete compiler process group."""
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if capture_output else None,
                                   stderr=subprocess.PIPE if capture_output else None,
                                   text=True, errors="replace", start_new_session=True)
    except OSError as error:
        return 127, "", str(error), False
    try:
        if capture_output:
            stdout, stderr = process.communicate(timeout=timeout)
        else:
            process.wait(timeout=timeout)
            stdout = stderr = ""
        return process.returncode, stdout, stderr, False
    except subprocess.TimeoutExpired:
        terminate_process_group(process)
        return -signal.SIGKILL, "", f"Timed out after {timeout}s", True
    except BaseException:
        terminate_process_group(process)
        raise
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()



def fetch_dependencies(cargo: str, project: Path, env: dict[str, str]) -> bool:
    """Use cached dependencies when available, otherwise fetch with a deadline."""
    # 1. Fast check if all dependencies are already cached offline
    offline_cmd = [cargo, "fetch", "--locked", "--offline", "--target", TARGET]
    rc, _, _, _ = run_bounded(offline_cmd, cwd=project, env=env, timeout=30, capture_output=True)
    if rc == 0:
        return True

    if env.get("CARGO_NET_OFFLINE", "").lower() in {"true", "1"}:
        log("WARN", "Cargo dependencies are not cached and CARGO_NET_OFFLINE is enabled")
        return False

    log("INFO", f"Missing dependencies; fetching from crates.io (timeout: {FETCH_TIMEOUT_S}s)...")
    fetch_env = env.copy()
    fetch_env["CARGO_HTTP_TIMEOUT"] = "30"
    fetch_env["CARGO_NET_RETRY"] = "2"
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
        log("WARN", f"Cargo dependency fetch exceeded {FETCH_TIMEOUT_S}s")
        return False
    if rc != 0:
        log("WARN", f"Cargo dependency fetch failed (exit {rc}):\n{stderr[-1000:].strip()}")
        return False
    return True


def build_native(project: Path, binary: Path, manifest_path: Path, version: str) -> bool:
    cargo = shutil.which("cargo")
    if not cargo:
        log("WARN", "Cargo is not installed; skipping native compilation")
        return False

    base = build_base()
    with tempfile.TemporaryDirectory(prefix="dusky-center-build-", dir=base) as temp:
        temp_dir = Path(temp)
        target_dir = temp_dir / "target"
        cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))

        env = os.environ.copy()
        env["CARGO_TARGET_DIR"] = str(target_dir)
        env["CARGO_HOME"] = str(cargo_home)
        env["CARGO_BUILD_BUILD_DIR"] = str(target_dir)
        env["TMPDIR"] = str(temp_dir)
        env["CARGO_HTTP_TIMEOUT"] = "15"
        env["CARGO_NET_RETRY"] = "1"
        env["CARGO_REGISTRIES_CRATES_IO_PROTOCOL"] = "sparse"
        env["RUSTUP_AUTO_INSTALL"] = "0"
        env["RUSTFLAGS"] = "-C target-cpu=native"
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
        env["CFLAGS"] = "-march=native -mtune=native -O2"
        env["CXXFLAGS"] = env["CFLAGS"]
        env["CPPFLAGS"] = ""
        try:
            _, available = memory_bytes()
        except (OSError, KeyError, ValueError):
            available = 2 * GIB
        compiler_budget = max(0, available - 3 * GIB)
        jobs = min(os.process_cpu_count() or 1, max(1, compiler_budget // (2 * GIB)))

        # Preflight: ensure dependencies are available before starting compilation
        if not fetch_dependencies(cargo, project, env):
            return False

        build_env = env.copy()
        build_env["CARGO_NET_OFFLINE"] = "true"
        build_env["RUSTUP_AUTO_INSTALL"] = "0"

        log("INFO", f"Compiling native release v{version} on tmpfs ({jobs} jobs)")
        before = source_digest(project)
        command = [cargo, "build", "--release", "--frozen", "--target", TARGET, "--jobs", str(jobs)]
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

        built = target_dir / TARGET / "release" / "dusky-center"
        if not binary_runs(built):
            log("WARN", "Built binary failed its executable smoke test")
            return False

        if source_digest(project) != before:
            log("WARN", "Sources changed during compilation; rerun setup")
            return False

        temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
        temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
        try:
            shutil.copy2(built, temporary_binary)
            temporary_binary.chmod(0o755)
            manifest = {
                "version": version,
                "target": TARGET,
                "target_cpu": "native",
                "cpu_signature": cpu_signature(),
                "source_sha256": before,
                "binary_sha256": sha256(temporary_binary),
            }
            temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            # Prepare both files before changing the working installation.
            os.replace(temporary_binary, binary)
            os.replace(temporary_manifest, manifest_path)
        except OSError as error:
            log("WARN", f"Could not install built binary: {error}")
            return False
        finally:
            temporary_binary.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
    return True


def link_system_binary(system_binary: Path, binary: Path) -> bool:
    temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
    try:
        if binary.is_symlink() and binary.readlink() == system_binary:
            return True
        temporary_binary.symlink_to(system_binary)
        os.replace(temporary_binary, binary)
        return True
    except OSError as error:
        log("WARN", f"Could not link system package binary: {error}")
        return False
    finally:
        temporary_binary.unlink(missing_ok=True)


def native_manifest(binary: Path, manifest_path: Path) -> dict | None:
    """Check CPU identity before executing a potentially incompatible binary."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (isinstance(manifest, dict)
                and manifest.get("target") == TARGET
                and manifest.get("target_cpu") == "native"
                and manifest.get("cpu_signature") == cpu_signature()
                and manifest.get("binary_sha256") == sha256(binary)
                and binary_runs(binary)):
            return manifest
    except (OSError, ValueError):
        pass
    return None


def install_defaults(project: Path, install_dir: Path) -> None:
    """Seed editable TOML without replacing any existing configuration."""
    source = project / "dusky_config.toml"
    if not source.is_file():
        source = SYSTEM_DEFAULTS
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dusky"
    config_dir.mkdir(parents=True, exist_ok=True)
    for destination in (config_dir / "dusky_config.toml", install_dir / "dusky_config.toml"):
        if source.is_file() and not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            try:
                shutil.copyfile(source, temporary)
                # Publish a complete file only if nobody has created it meanwhile.
                os.link(temporary, destination)
            except FileExistsError:
                pass
            finally:
                temporary.unlink(missing_ok=True)


def packaged_source_matches(project: Path) -> bool:
    try:
        return SYSTEM_SOURCE_DIGEST.read_text(encoding="utf-8").strip() == source_digest(project)
    except OSError:
        return False


def setup(project: Path, install_dir: Path, force: bool) -> int:
    binary = install_dir / "dusky-center"
    manifest_path = install_dir / "binary_manifest.json"
    version = get_project_version(project) if (project / "Cargo.toml").is_file() else None
    old_manifest = native_manifest(binary, manifest_path)
    current = bool(version and old_manifest
                   and old_manifest.get("version") == version
                   and old_manifest.get("source_sha256") == source_digest(project)
                   and binary_version(binary) == version)
    selected = current and not force
    if selected:
        log("OK", f"Using verified native build for this CPU (v{version})")
    elif not force and binary_runs(SYSTEM_BINARY) and (
        version is None or (binary_version(SYSTEM_BINARY) == version and packaged_source_matches(project))
    ):
        selected = link_system_binary(SYSTEM_BINARY, binary)
        if selected:
            manifest_path.unlink(missing_ok=True)
            log("OK", "Using matching generic ISO package; no compilation needed")
    if not selected and version:
        try:
            selected = build_native(project, binary, manifest_path, version)
        except OSError as error:
            log("WARN", f"Native build failed: {error}")
        if selected:
            log("OK", f"Installed native Dusky Center (v{version})")
    if not selected and old_manifest:
        log("WARN", "Keeping the previous working native binary; rerun setup to update")
        selected = True
    if not selected and binary_runs(SYSTEM_BINARY):
        log("WARN", "Using the generic ISO package; rerun setup for CPU-specific compilation")
        selected = link_system_binary(SYSTEM_BINARY, binary)
        if selected:
            manifest_path.unlink(missing_ok=True)
    if not selected:
        log("ERR", "No native or packaged Dusky Center binary is available")
        return 1
    install_defaults(project, install_dir)
    launcher = Path.home() / ".local/bin/dusky-center"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if not link_system_binary(binary, launcher):
        return 1
    log("OK", f"Dusky Center ready at {launcher}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-rebuild", action="store_true",
                        help="Rebuild even when source and CPU fingerprints match")
    args = parser.parse_args(argv)
    if platform.machine() != "x86_64":
        log("ERR", "The Dusky ISO supports x86-64 only")
        return 1
    home = Path.home()
    project = home / "user_scripts/dusky_system/dusky_center"
    install_dir = home / ".local/share/dusky/dusky_center"
    install_dir.mkdir(parents=True, exist_ok=True)
    with (install_dir / ".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return setup(project, install_dir, args.force_rebuild)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as error:
        log("ERR", f"Dusky Center setup failed: {error}")
        sys.exit(1)
