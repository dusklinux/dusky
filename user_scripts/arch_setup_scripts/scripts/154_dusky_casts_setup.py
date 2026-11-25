#!/usr/bin/env python3
"""Install the matching ISO binary, or compile Dusky Casts for this CPU; never launch it."""

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

APP_NAME = "dusky_casts"
TARGET = "x86_64-unknown-linux-gnu"
DEFAULT_PROJECT = Path(__file__).resolve().parents[2] / "dusky/dusky_casts"
SYSTEM_BINARY = Path("/usr/bin/dusky_casts")
SYSTEM_SOURCE_DIGEST = Path("/usr/share/dusky_casts/source.sha256")
SYSTEM_DESKTOP = Path("/usr/share/applications/dusky_casts.desktop")
SYSTEM_ICON = Path("/usr/share/icons/hicolor/scalable/apps/dusky_casts.svg")
BUILD_RECIPE = "casts-native-v1-frozen-tmpfs"
EXTRA_DIGEST_DIRS = ("proto", "assets", "data")


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 7200) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


FETCH_TIMEOUT_S = env_int("DUSKY_CASTS_FETCH_TIMEOUT", 120, maximum=1800)
BUILD_TIMEOUT_S = env_int("DUSKY_CASTS_BUILD_TIMEOUT", 7200, maximum=7200)


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", file=sys.stderr if level == "ERR" else sys.stdout, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_digest(project: Path = DEFAULT_PROJECT) -> str:
    return build_support.source_digest(
        project,
        recipe=BUILD_RECIPE,
        include_fonts=False,
        extra_dirs=EXTRA_DIGEST_DIRS,
    )


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
        return result.returncode == 0 and APP_NAME in result.stdout
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
            tokens = result.stdout.strip().split()
            if len(tokens) >= 2 and tokens[0] == APP_NAME:
                return tokens[1].lstrip("vV")
        return None
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_signature() -> str:
    """Invalidate a local native build if a home directory moves to another CPU."""
    text = Path("/proc/cpuinfo").read_text(encoding="utf-8")
    first_cpu = text.split("\n\n", 1)[0]
    fields = ("vendor_id", "cpu family", "model", "stepping", "flags")
    identity = "\n".join(
        line for line in first_cpu.splitlines()
        if line.partition(":")[0].strip() in fields
    )
    if not identity:
        raise ValueError("No CPU identity/features in /proc/cpuinfo")
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


def run_bounded(
    command: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> tuple[int, str, str, bool]:
    """Bound fetch/build time and clean up the complete compiler process group."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            text=True,
            errors="replace",
            start_new_session=True,
            pass_fds=build_support.build_lock_fds(),
        )
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


def check_build_tools() -> tuple[str, str] | None:
    """Verify required native build tools and sqlite3 headers before compiling."""
    missing: list[str] = []
    cargo = shutil.which("cargo")
    rustc = shutil.which("rustc")
    protoc = shutil.which("protoc")
    pkg_config = shutil.which("pkg-config")

    for name, resolved in (
        ("cargo", cargo),
        ("rustc", rustc),
        ("protoc", protoc),
        ("pkg-config", pkg_config),
    ):
        if not resolved:
            missing.append(name)

    if pkg_config:
        try:
            probe = subprocess.run(
                [pkg_config, "--exists", "sqlite3"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=10,
                check=False,
            )
            if probe.returncode != 0:
                missing.append("sqlite3 (pkg-config)")
        except (OSError, subprocess.TimeoutExpired):
            missing.append("sqlite3 (pkg-config)")

    if missing:
        log("WARN", f"Missing build dependencies ({', '.join(missing)}); skipping compilation")
        return None
    assert cargo is not None and rustc is not None
    return cargo, rustc


def fetch_dependencies(cargo: str, project: Path, env: dict[str, str]) -> bool:
    """Use cached dependencies when available, otherwise fetch with a deadline."""
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


def make_cargo_env(
    workspace: build_support.Workspace,
    rustc: str,
    *,
    generic: bool,
) -> dict[str, str]:
    temp_dir = workspace.path
    target_dir = temp_dir / "target"
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))

    env = os.environ.copy()
    env.pop("CARGO_ENCODED_RUSTFLAGS", None)
    for key in tuple(env):
        if key.startswith("CARGO_PROFILE_RELEASE_"):
            del env[key]

    cpu = "x86-64" if generic else "native"
    cflags = "-march=x86-64 -mtune=generic -O2" if generic else "-march=native -mtune=native -O2"
    env.update(
        CARGO_TARGET_DIR=str(target_dir),
        CARGO_BUILD_BUILD_DIR=str(target_dir),
        CARGO_HOME=str(cargo_home),
        TMPDIR=str(temp_dir),
        CARGO_HTTP_TIMEOUT="15",
        CARGO_NET_RETRY="1",
        CARGO_REGISTRIES_CRATES_IO_PROTOCOL="sparse",
        CARGO_INCREMENTAL="0",
        RUSTUP_AUTO_INSTALL="0",
        RUSTC=rustc,
        RUSTC_WRAPPER="",
        RUSTC_WORKSPACE_WRAPPER="",
        RUSTFLAGS=f"-C target-cpu={cpu}",
        CFLAGS=cflags,
        CXXFLAGS=cflags,
        CPPFLAGS="",
    )
    return env


@build_support.temporary_build(
    "dusky_casts-build-",
    artifact_bytes=3 * build_support.GIB,
    compiler_bytes=2 * build_support.GIB,
)
def build_binary(
    project: Path,
    binary: Path,
    manifest_path: Path,
    version: str,
    *,
    generic: bool = False,
    workspace: build_support.Workspace,
) -> bool:
    tools = check_build_tools()
    if tools is None:
        return False
    cargo, rustc = tools

    env = make_cargo_env(workspace, rustc, generic=generic)
    jobs = workspace.jobs

    if not fetch_dependencies(cargo, project, env):
        return False

    build_env = env.copy()
    build_env["CARGO_NET_OFFLINE"] = "true"
    build_env["RUSTUP_AUTO_INSTALL"] = "0"

    target_cpu = "x86-64" if generic else "native"
    log("INFO", f"Compiling {target_cpu} release v{version} in {workspace.storage} ({jobs} jobs)")
    before = source_digest(project)
    command = [
        cargo,
        "build",
        "--release",
        "--frozen",
        "--target",
        TARGET,
        "--jobs",
        str(jobs),
        "--bin",
        APP_NAME,
    ]
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

    built = workspace.path / "target" / TARGET / "release" / APP_NAME
    if not binary_runs(built) or binary_version(built) != version:
        log("WARN", "Built binary failed its executable smoke test")
        return False

    if source_digest(project) != before:
        log("WARN", "Sources changed during compilation; rerun setup")
        return False

    binary.parent.mkdir(parents=True, exist_ok=True)
    temporary_binary = binary.with_name(f".{binary.name}.{os.getpid()}.tmp")
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
    try:
        shutil.copy2(built, temporary_binary)
        temporary_binary.chmod(0o755)
        manifest = {
            "recipe": BUILD_RECIPE,
            "version": version,
            "target": TARGET,
            "target_cpu": target_cpu,
            "cpu_signature": None if generic else cpu_signature(),
            "source_sha256": before,
            "binary_sha256": sha256(temporary_binary),
        }
        temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary_binary, binary)
        os.replace(temporary_manifest, manifest_path)
    except OSError as error:
        log("WARN", f"Could not install built binary: {error}")
        return False
    finally:
        temporary_binary.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)
    return True


@build_support.temporary_build(
    "dusky_casts-check-",
    artifact_bytes=3 * build_support.GIB,
    compiler_bytes=2 * build_support.GIB,
)
def run_dev_action(project: Path, action: str, *, workspace: build_support.Workspace) -> int:
    tools = check_build_tools()
    if tools is None:
        return 1
    cargo, rustc = tools

    env = make_cargo_env(workspace, rustc, generic=False)
    if not fetch_dependencies(cargo, project, env):
        return 1

    run_env = env.copy()
    run_env["CARGO_NET_OFFLINE"] = "true"
    common = ["--frozen", "--target", TARGET, "--jobs", str(workspace.jobs)]
    if action == "check":
        command = [cargo, "check", *common]
    elif action == "clippy":
        command = [cargo, "clippy", *common, "--all-targets", "--", "-D", "warnings"]
    elif action == "test":
        command = [cargo, "test", *common]
    else:
        raise ValueError(f"Unsupported dev action: {action}")

    log("INFO", f"Running cargo {action} in {workspace.storage} ({workspace.jobs} jobs)")
    rc, stdout, stderr, timed_out = run_bounded(
        command,
        cwd=project,
        env=run_env,
        timeout=BUILD_TIMEOUT_S,
        capture_output=True,
    )
    if stdout:
        print(stdout, end="", flush=True)
    if stderr:
        print(stderr, file=sys.stderr, end="", flush=True)
    if timed_out:
        log("ERR", f"cargo {action} exceeded {BUILD_TIMEOUT_S}s")
        return 124
    if rc != 0:
        workspace.retry_on_disk(rc, stdout + "\n" + stderr)
        return rc if rc > 0 else 1
    return 0


def link_binary(target: Path, destination: Path) -> bool:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        if destination.is_symlink() and destination.readlink() == target:
            return True
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.symlink_to(target)
        os.replace(temporary, destination)
        return True
    except OSError as error:
        log("WARN", f"Could not link binary {destination} -> {target}: {error}")
        return False
    finally:
        temporary.unlink(missing_ok=True)


def install_file_atomic(source: Path, destination: Path, *, mode: int = 0o644) -> bool:
    """Atomically install source to destination if missing or different; return True if changed."""
    data = source.read_bytes()
    try:
        if destination.is_file() and not destination.is_symlink():
            if destination.read_bytes() == data and (destination.stat().st_mode & 0o777) == mode:
                return False
    except OSError:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.chmod(mode)
        os.replace(temporary, destination)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def unlink_if_present(path: Path) -> bool:
    """Remove a file or symlink (even dangling); return True if an entry was removed."""
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as error:
        log("WARN", f"Could not remove stale path {path}: {error}")
        return False


def sync_desktop_and_cleanup(project: Path, home: Path) -> None:
    """Remove stale legacy duskcasts files and deduplicate desktop/icon assets."""
    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    apps_dir = data_home / "applications"
    icons_dir = data_home / "icons/hicolor/scalable/apps"
    licenses_dir = data_home / "licenses"

    apps_changed = False

    # Clean up legacy duskcasts launcher, desktop files, icon, and license.
    unlink_if_present(home / ".local/bin/duskcasts")
    for legacy_desktop in (
        apps_dir / "duskcasts.desktop",
        apps_dir / "io.github.dusklinux.DuskCasts.desktop",
    ):
        if unlink_if_present(legacy_desktop):
            apps_changed = True
    unlink_if_present(icons_dir / "io.github.dusklinux.DuskCasts.svg")
    unlink_if_present(licenses_dir / "duskcasts/LICENSE.md")
    try:
        (licenses_dir / "duskcasts").rmdir()
    except OSError:
        pass

    user_desktop = apps_dir / f"{APP_NAME}.desktop"
    user_icon = icons_dir / f"{APP_NAME}.svg"
    user_license = licenses_dir / APP_NAME / "LICENSE.md"

    if SYSTEM_DESKTOP.is_file() and SYSTEM_ICON.is_file():
        # System pacman package owns the launcher entry and icon; avoid duplicate menu items.
        if unlink_if_present(user_desktop):
            apps_changed = True
    else:
        src_desktop = project / "data" / f"{APP_NAME}.desktop"
        src_icon = project / "data" / f"{APP_NAME}.svg"
        src_license = project / "LICENSE.md"
        if src_desktop.is_file() and install_file_atomic(src_desktop, user_desktop, mode=0o644):
            apps_changed = True
        if src_icon.is_file():
            install_file_atomic(src_icon, user_icon, mode=0o644)
        if src_license.is_file():
            install_file_atomic(src_license, user_license, mode=0o644)

    if apps_changed and apps_dir.is_dir():
        updater = shutil.which("update-desktop-database")
        if updater:
            try:
                subprocess.run(
                    [updater, str(apps_dir)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass


def native_manifest(binary: Path, manifest_path: Path) -> dict | None:
    """Check CPU identity before executing a potentially incompatible binary."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            isinstance(manifest, dict)
            and manifest.get("target") == TARGET
            and manifest.get("target_cpu") == "native"
            and manifest.get("cpu_signature") == cpu_signature()
            and manifest.get("binary_sha256") == sha256(binary)
            and binary_runs(binary)
        ):
            return manifest
    except (OSError, ValueError):
        pass
    return None


def packaged_source_matches(project: Path = DEFAULT_PROJECT) -> bool:
    try:
        return SYSTEM_SOURCE_DIGEST.read_text(encoding="utf-8").strip() == source_digest(project)
    except (OSError, ValueError):
        return False


def setup(
    project: Path,
    install_dir: Path,
    *,
    force: bool = False,
    native: bool = False,
    home: Path | None = None,
) -> int:
    user_home = home or Path.home()
    binary = install_dir / APP_NAME
    manifest_path = install_dir / "binary_manifest.json"
    version = get_project_version(project) if (project / "Cargo.toml").is_file() else None
    old_manifest = native_manifest(binary, manifest_path)
    current = bool(
        version
        and old_manifest
        and old_manifest.get("recipe") == BUILD_RECIPE
        and old_manifest.get("version") == version
        and old_manifest.get("source_sha256") == source_digest(project)
        and binary_version(binary) == version
    )

    selected = False
    if not force and not native:
        if current:
            selected = True
            log("OK", f"Using verified native build for this CPU (v{version})")
        elif binary_runs(SYSTEM_BINARY) and (
            version is None
            or (binary_version(SYSTEM_BINARY) == version and packaged_source_matches(project))
        ):
            selected = link_binary(SYSTEM_BINARY, binary)
            if selected:
                manifest_path.unlink(missing_ok=True)
                log("OK", "Using matching generic ISO package; no compilation needed")

    if not selected and version:
        try:
            selected = build_binary(project, binary, manifest_path, version, generic=False)
        except OSError as error:
            log("WARN", f"Native build failed: {error}")
        if selected:
            log("OK", f"Installed native Dusky Casts (v{version})")

    if not selected and old_manifest:
        log("WARN", "Keeping the previous working native binary; rerun setup to update")
        selected = True

    if not selected and binary_runs(SYSTEM_BINARY):
        log("WARN", "Using the generic ISO package; rerun setup for CPU-specific compilation")
        selected = link_binary(SYSTEM_BINARY, binary)
        if selected:
            manifest_path.unlink(missing_ok=True)

    if not selected:
        log("ERR", "No native or packaged Dusky Casts binary is available")
        return 1

    launcher = user_home / ".local/bin" / APP_NAME
    if not link_binary(binary, launcher):
        return 1

    sync_desktop_and_cleanup(project, user_home)
    log("OK", f"Dusky Casts ready at {launcher}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force-rebuild",
        action="store_true",
        help="Rebuild natively even when source and CPU fingerprints match",
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="Compile a native CPU-tuned binary instead of reusing the generic ISO package",
    )
    parser.add_argument(
        "--generic",
        action="store_true",
        help="Build a standalone baseline x86-64 binary outside the source tree",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination directory for --generic builds (must be outside project and install dirs)",
    )
    dev_group = parser.add_mutually_exclusive_group()
    dev_group.add_argument("--check", action="store_true", help="Run cargo check --frozen on tmpfs")
    dev_group.add_argument("--clippy", action="store_true", help="Run cargo clippy --frozen on tmpfs")
    dev_group.add_argument("--test", action="store_true", help="Run cargo test --frozen on tmpfs")
    args = parser.parse_args(argv)

    if platform.machine() != "x86_64":
        log("ERR", "The Dusky ISO supports x86-64 only")
        return 1

    dev_action = next((name for name in ("check", "clippy", "test") if getattr(args, name)), None)
    if args.generic and (args.native or args.force_rebuild or dev_action):
        parser.error("--generic cannot be combined with --native, --force-rebuild, or dev checks")
    if args.generic and args.output_dir is None:
        parser.error("--generic requires --output-dir outside the source tree")
    if args.output_dir is not None and not args.generic:
        parser.error("--output-dir is only used with --generic")
    if dev_action and (args.native or args.force_rebuild):
        parser.error(f"--{dev_action} cannot be combined with --native or --force-rebuild")

    home = Path.home()
    project = DEFAULT_PROJECT
    install_dir = home / ".local/share/dusky" / APP_NAME

    if dev_action:
        if not (project / "Cargo.toml").is_file():
            log("ERR", f"Project Cargo.toml not found at {project}")
            return 1
        return run_dev_action(project, dev_action)

    if args.generic:
        if not (project / "Cargo.toml").is_file():
            log("ERR", f"Project Cargo.toml not found at {project}")
            return 1
        output_dir = args.output_dir.resolve()
        if output_dir.is_relative_to(project.resolve()):
            parser.error("--output-dir must be outside the source directory")
        if output_dir.is_relative_to(install_dir.resolve()):
            parser.error("--output-dir must be outside ~/.local/share/dusky/dusky_casts")
        output_dir.mkdir(parents=True, exist_ok=True)
        version = get_project_version(project)
        with (output_dir / ".build.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ok = build_binary(
                project,
                output_dir / APP_NAME,
                output_dir / "binary_manifest.json",
                version,
                generic=True,
            )
            return 0 if ok else 1

    install_dir.mkdir(parents=True, exist_ok=True)
    with (install_dir / ".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return setup(
            project,
            install_dir,
            force=args.force_rebuild,
            native=args.native,
            home=home,
        )


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as error:
        log("ERR", f"Dusky Casts setup failed: {error}")
        sys.exit(1)
