#!/usr/bin/env python3
"""Use a current ISO package or build Dusky Papers for this CPU."""

import argparse
import codecs
import hashlib
import json
import os
import platform
import re
import selectors
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
SYSTEM_BINARY = Path("/usr/bin/dusky-papers")
SYSTEM_SOURCE_DIGEST = Path("/usr/share/dusky-papers/source.sha256")


def env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 3600) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


BUILD_RECIPE = "native-v3-frozen-sparse"
FETCH_TIMEOUT_S = env_int("DUSKY_PAPERS_FETCH_TIMEOUT", 120, maximum=1800)
BUILD_TIMEOUT_S = env_int("DUSKY_PAPERS_BUILD_TIMEOUT", 7200, maximum=7200)
# Maximum cache inactivity, rather than total time for a growing collection.
CACHE_TIMEOUT_S = env_int("DUSKY_PAPERS_CACHE_TIMEOUT", 120, maximum=900)


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
        return result.returncode == 0 and "dusky-papers" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def get_project_version(project: Path) -> str:
    cargo_toml = project / "Cargo.toml"
    with cargo_toml.open("rb") as stream:
        return tomllib.load(stream)["package"]["version"]


def parse_version(v: str | None) -> tuple:
    if not v:
        return (-1, -1, -1, ())
    match = re.fullmatch(r"[vV]?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", v.strip())
    if not match:
        return (-1, -1, -1, ())
    prerelease = match.group(4)
    suffix = (0, tuple((0, int(part)) if part.isdigit() else (1, part)
                       for part in prerelease.split("."))) if prerelease else (1, ())
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), suffix)


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
        if not isinstance(manifest, dict):
            return False
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


def packaged_source_matches(project: Path) -> bool:
    try:
        return SYSTEM_SOURCE_DIGEST.read_text().strip() == source_digest(project)
    except OSError:
        return False


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
    inactivity_timeout: bool = False,
) -> tuple[int, str, str, bool]:
    """Run with a total or output-inactivity deadline and process-group cleanup."""
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture_output or inactivity_timeout else None,
            stderr=subprocess.PIPE if capture_output or inactivity_timeout else None,
            text=True,
            errors="replace",
            start_new_session=True,
            pass_fds=build_support.build_lock_fds(),
        )
    except OSError as error:
        return (
            127,
            "",
            f"Could not start {command[0]!r}: {error}",
            False,
        )

    try:
        if inactivity_timeout:
            # Cache work scales with the number of missing images. Bound stalls,
            # not the total duration of a healthy, progressing build.
            deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                for stream, output in ((process.stdout, sys.stdout), (process.stderr, sys.stderr)):
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                    selector.register(stream, selectors.EVENT_READ, (output, decoder))
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    for key, _ in selector.select(remaining):
                        output, decoder = key.data
                        data = os.read(key.fd, 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            output.write(decoder.decode(b"", final=True))
                            output.flush()
                        else:
                            output.write(decoder.decode(data))
                            output.flush()
                            deadline = time.monotonic() + timeout
            process.wait(timeout=max(0, deadline - time.monotonic()))
            stdout = stderr = ""
        elif capture_output:
            stdout, stderr = process.communicate(timeout=timeout)
        else:
            process.wait(timeout=timeout)
            stdout = stderr = ""
        if process.returncode:
            terminate_process_group(process)
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
    finally:
        if inactivity_timeout:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


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
    for filename in ("config.toml", "credentials.toml"):
        source = existing / filename
        if source.is_file():
            try:
                shutil.copyfile(source, cargo_home / filename)
            except OSError as error:
                log("WARN", f"Could not copy Cargo {filename}: {error}")
    return cargo_home


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


@build_support.temporary_build("dusky-papers-build-")
def build_native(project: Path, binary: Path, manifest_path: Path, version: str,
                 *, workspace: build_support.Workspace) -> bool:
    cargo = shutil.which("cargo")
    if not cargo:
        log("WARN", "Cargo is not installed; skipping native compilation")
        return False

    temp_dir = workspace.path
    target_dir = temp_dir / "target"
    cargo_home = get_cargo_home(temp_dir)

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
    jobs = workspace.jobs

    # Preflight: ensure dependencies are available before starting compilation
    if not fetch_dependencies(cargo, project, env):
        return False

    build_env = env.copy()
    build_env["CARGO_NET_OFFLINE"] = "true"
    build_env["RUSTUP_AUTO_INSTALL"] = "0"

    before = source_digest(project)
    log("INFO", f"Compiling native release binary for this CPU (v{version})")
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
        workspace.retry_on_disk(returncode, stderr)
        log("WARN", f"Cargo build failed:\n{stderr[-12000:]}")
        return False

    built = target_dir / TARGET / "release" / "dusky-papers"
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
        temporary_manifest.write_text(json.dumps(manifest, indent=2) + "\n")
        os.replace(temporary_binary, binary)
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
    project = home / "user_scripts/images/dusky_papers"
    source_available = (project / "Cargo.toml").is_file()
    try:
        project_ver = get_project_version(project) if source_available else None
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        log("WARN", f"Could not read Dusky Papers package version; skipping setup: {error}")
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version", "-v", action="version",
        version=f"dusky_papers_setup {project_ver or 'package-only'}",
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

    install_dir = home / ".local/share/dusky/dusky_papers"
    binary = install_dir / "dusky-papers"
    manifest_path = install_dir / "binary_manifest.json"
    local_bin = home / ".local/bin/dusky-papers"
    thumb_dir = home / ".cache/dusky_images/dusky_papers/thumbs"
    settings_dir = home / ".config/dusky/settings/dusky_theme"

    if platform.machine() != "x86_64":
        log("WARN", "This Dusky Papers package targets x86-64 only; skipping setup")
        return 0
    try:
        for directory in (thumb_dir, settings_dir, install_dir, local_bin.parent):
            directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        log("WARN", f"Could not prepare Dusky Papers directories; skipping setup: {error}")
        return 0

    system_ok = binary_runs(SYSTEM_BINARY)
    system_ver = binary_version(SYSTEM_BINARY) if system_ok else None

    selected = False
    package_newer = bool(
        system_ver and project_ver and parse_version(system_ver) > parse_version(project_ver)
    )

    # Keep a verified native build when the generic ISO package also exists.
    if (
        not package_newer
        and project_ver is not None
        and native_binary_valid(project, binary, manifest_path, project_ver)
    ):
        log("OK", f"Using verified native build for this CPU (v{project_ver})")
        selected = True

    if (
        not selected
        and system_ver
        and (
            project_ver is None
            or package_newer
            or (
                parse_version(system_ver) == parse_version(project_ver)
                and packaged_source_matches(project)
            )
        )
    ):
        log("OK", f"Using ISO package binary at {SYSTEM_BINARY} (v{system_ver})")
        if link_system_binary(SYSTEM_BINARY, binary):
            manifest_path.unlink(missing_ok=True)
            selected = True

    # Compile when the installed ISO package does not match the current source.
    if not selected and project_ver is not None:
        if system_ok:
            log("INFO", f"System binary at {SYSTEM_BINARY} does not match the source; attempting native build")
        try:
            built_ok = build_native(project, binary, manifest_path, project_ver)
        except OSError as error:
            log("WARN", f"Could not complete native build; using an available package if possible: {error}")
            built_ok = False
        if built_ok:
            log("OK", f"Installed newly built native binary (v{project_ver})")
            selected = True

    # A working ISO package remains usable if source or native compilation fails.
    if not selected and system_ok:
        log("WARN", f"Using ISO package binary (v{system_ver or 'unknown'}) because no current native build is available")
        if link_system_binary(SYSTEM_BINARY, binary):
            manifest_path.unlink(missing_ok=True)
            selected = True

    if not selected:
        log("WARN", "No runnable packaged or native Dusky Papers binary is available; skipping setup")
        return 0

    if binary_runs(binary):
        temporary_link = local_bin.with_name(f".{local_bin.name}.{os.getpid()}.tmp")
        try:
            if not (local_bin.is_symlink() and local_bin.readlink() == binary):
                temporary_link.symlink_to(binary)
                os.replace(temporary_link, local_bin)
        except OSError as error:
            temporary_link.unlink(missing_ok=True)
            log("WARN", f"Could not create launcher symlink: {error}")
            return 0
    else:
        log("WARN", "No runnable Dusky Papers binary available; skipping launcher symlink")
        return 0

    cache_mode = "--rebuild-cache" if args.rebuild_cache else "--build-cache"
    log("INFO", "Regenerating all previews" if args.rebuild_cache else "Generating missing or stale previews")
    returncode, _, _, timed_out = run_bounded(
        [str(binary), cache_mode],
        timeout=CACHE_TIMEOUT_S,
        capture_output=False,
        inactivity_timeout=True,
    )
    if timed_out:
        log("WARN", f"Cache generation made no progress for {CACHE_TIMEOUT_S}s and was terminated; continuing update")
        return 0
    if returncode != 0:
        log("WARN", f"Thumbnail generation failed (exit {returncode}); continuing update")
        return 0
    log("OK", "Dusky Papers setup complete")
    return 0


if __name__ == "__main__":
    # Unwind runner and TemporaryDirectory cleanup on service/updater cancellation.
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        sys.exit(main())
    except Exception as error:
        log("WARN", f"Dusky Papers setup failed; continuing update: {error}")
        sys.exit(0)
