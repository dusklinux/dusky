#!/usr/bin/env python3
"""Reuse a matching Dusky Snap ISO binary, or build for this CPU with temporary storage."""

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
import time
import tomllib
from importlib import import_module
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
build_support = import_module("147_rust_compiles_shared_NOT_STANDALONE")

TARGET = "x86_64-unknown-linux-gnu"
SYSTEM_BINARY = Path("/usr/bin/dusky_snap")
SYSTEM_SOURCE_DIGEST = Path("/usr/share/dusky-snap/source.sha256")
BUILD_RECIPE = "snap-rust-v3-native-first-frozen-tmpfs"


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 3600) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


BUILD_TIMEOUT_S = env_int("DUSKY_SNAP_BUILD_TIMEOUT", 7200, maximum=7200)


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
        return result.returncode == 0 and "dusky_snap" in result.stdout
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


def run_bounded(command: list[str], *, timeout: int, cwd: Path | None = None,
                env: dict[str, str] | None = None, capture_output: bool = False
                ) -> tuple[int, str, str, bool]:
    """Bound fetch/build time and clean up the complete compiler process group."""
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE if capture_output else None,
                                   stderr=subprocess.PIPE if capture_output else None,
                                   text=True, errors="replace", start_new_session=True,
                                   pass_fds=build_support.build_lock_fds())
    except OSError as error:
        return 127, "", str(error), False
    try:
        if capture_output:
            stdout, stderr = process.communicate(timeout=timeout)
        else:
            process.wait(timeout=timeout)
            stdout = stderr = ""
        if process.returncode:
            terminate_process_group(process)
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
    """Installation is offline; the ISO must supply a package or cached crates."""
    rc, _, stderr, _ = run_bounded(
        [cargo, "fetch", "--locked", "--offline", "--target", TARGET],
        cwd=project, env=env, timeout=30, capture_output=True,
    )
    if rc:
        log("WARN", f"Offline dependencies unavailable: {stderr[-1000:].strip()}")
        return False
    return True


@build_support.temporary_build("dusky-snap-build-")
def build_native(project: Path, binary: Path, manifest_path: Path, version: str,
                 *, workspace: build_support.Workspace) -> bool:
    cargo = shutil.which("cargo")
    if not cargo:
        log("WARN", "Cargo is not installed; skipping native compilation")
        return False

    temp_dir = workspace.path
    target_dir = temp_dir / "target"
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))

    env = os.environ.copy()
    env["CARGO_TARGET_DIR"] = str(target_dir)
    env["CARGO_HOME"] = str(cargo_home)
    env["CARGO_BUILD_BUILD_DIR"] = str(target_dir)
    env["TMPDIR"] = str(temp_dir)
    env["CARGO_NET_OFFLINE"] = "true"
    env["RUSTUP_AUTO_INSTALL"] = "0"
    env["RUSTFLAGS"] = "-C target-cpu=native"
    env.pop("CARGO_ENCODED_RUSTFLAGS", None)
    env["CFLAGS"] = "-march=native -mtune=native -O2"
    env["CXXFLAGS"] = env["CFLAGS"]
    env["CPPFLAGS"] = ""
    jobs = workspace.jobs

    # Verify the ISO dependency cache before starting compilation
    if not fetch_dependencies(cargo, project, env):
        return False

    build_env = env.copy()
    build_env["CARGO_NET_OFFLINE"] = "true"
    build_env["RUSTUP_AUTO_INSTALL"] = "0"

    log("INFO", f"Compiling native release v{version} in {workspace.storage} ({jobs} jobs)")
    before = source_digest(project)
    command = [cargo, "build", "--release", "--frozen", "--target", TARGET, "--jobs", str(jobs), "--bin", "dusky_snap"]
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
        workspace.retry_on_disk(returncode, stderr)
        log("WARN", f"Cargo build failed:\n{stderr[-12000:]}")
        return False

    built = target_dir / TARGET / "release" / "dusky_snap"
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
    """Seed editable defaults and add a launcher without replacing associations."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "dusky/snap"
    config_dir.mkdir(parents=True, exist_ok=True)
    source = project / "config.json"
    if not source.is_file():
        source = Path("/usr/share/dusky-snap/config.json")
    destination = config_dir / "config.json"
    if source.is_file() and not destination.exists():
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.link(temporary, destination)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    source = project / "dusky_snap.desktop"
    if not source.is_file():
        source = Path("/usr/share/applications/dusky_snap.desktop")
    if source.is_file():
        applications = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
        applications.mkdir(parents=True, exist_ok=True)
        destination = applications / source.name
        temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def packaged_source_matches(project: Path) -> bool:
    try:
        return SYSTEM_SOURCE_DIGEST.read_text(encoding="utf-8").strip() == source_digest(project)
    except OSError:
        return False


def setup(project: Path, install_dir: Path, force: bool) -> int:
    binary = install_dir / "dusky_snap"
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
            log("OK", f"Installed native Dusky Snap (v{version})")
    if not selected and old_manifest:
        log("WARN", "Keeping the previous working native binary; rerun setup to update")
        selected = True
    if not selected and binary_runs(SYSTEM_BINARY):
        log("WARN", "Using the generic ISO package; rerun setup for CPU-specific compilation")
        selected = link_system_binary(SYSTEM_BINARY, binary)
        if selected:
            manifest_path.unlink(missing_ok=True)
    if not selected:
        log("ERR", "No native or packaged Dusky Snap binary is available")
        return 1
    install_defaults(project, install_dir)
    launcher = Path.home() / ".local/bin/dusky_snap"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if not link_system_binary(binary, launcher):
        return 1
    old_launcher = launcher.with_name("dusky-snap")
    old_binary = install_dir / "dusky-snap"
    if old_launcher.is_symlink() and old_launcher.resolve() == old_binary.resolve():
        old_launcher.unlink()
    old_binary.unlink(missing_ok=True)
    applications = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
    (applications / "dusky-snap.desktop").unlink(missing_ok=True)
    log("OK", f"Dusky Snap ready at {launcher}")
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
    project = home / "user_scripts/images/dusky_snap"
    install_dir = home / ".local/share/dusky/dusky_snap"
    install_dir.mkdir(parents=True, exist_ok=True)
    with (install_dir / ".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return setup(project, install_dir, args.force_rebuild)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as error:
        log("ERR", f"Dusky Snap setup failed: {error}")
        sys.exit(1)
