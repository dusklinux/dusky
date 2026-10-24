#!/usr/bin/env python3
"""Install Dusky Network, or build a generic ISO binary; compile offline, preferring tmpfs."""

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
import tomllib
from importlib import import_module
from pathlib import Path

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))
build_support = import_module("147_rust_compiles_shared_NOT_STANDALONE")

SYSTEM_BINARY = Path("/usr/bin/dusky-network")
SYSTEM_MANIFEST = Path("/usr/share/dusky-network/binary_manifest.json")
TARGET = "x86_64-unknown-linux-gnu"
RECIPE = "dusky-network-tmpfs-v2"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_digest() -> str:
    files = [PROJECT / name for name in ("Cargo.toml", "Cargo.lock", "setup.py", "147_rust_compiles_shared_NOT_STANDALONE.py")]
    files.extend(sorted(path for path in (PROJECT / "src").rglob("*") if path.is_file()))
    for name in ("build.rs", ".cargo/config", ".cargo/config.toml", "rust-toolchain.toml", "rust-toolchain"):
        if (PROJECT / name).is_file():
            files.append(PROJECT / name)
    result = hashlib.sha256(RECIPE.encode())
    for path in files:
        result.update(str(path.relative_to(PROJECT)).encode())
        result.update(b"\0")
        result.update(bytes.fromhex(digest(path)))
    return result.hexdigest()


def cpu_signature() -> str:
    first = Path("/proc/cpuinfo").read_text(encoding="utf-8").split("\n\n", 1)[0]
    fields = {"vendor_id", "cpu family", "model", "stepping", "flags", "Features", "CPU implementer", "CPU part"}
    identity = "\n".join(line for line in first.splitlines() if line.partition(":")[0].strip() in fields)
    if not identity:
        raise ValueError("No CPU identity/features in /proc/cpuinfo")
    return hashlib.sha256(identity.encode()).hexdigest()


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 1200) -> str:
    process = subprocess.Popen(command, cwd=PROJECT, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", start_new_session=True,
                               pass_fds=build_support.build_lock_fds())
    try:
        output, _ = process.communicate(timeout=timeout)
        if process.returncode:
            termination = " (SIGKILL)" if process.returncode == -signal.SIGKILL else ""
            raise RuntimeError(f"{' '.join(command)} failed ({process.returncode}){termination}:\n{output}")
        return output.strip()
    except BaseException:
        # A failed/killed Cargo leader can leave live compiler descendants.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise


def build(args: argparse.Namespace) -> None:
    cargo, rustc = shutil.which("cargo"), shutil.which("rustc")
    if not cargo or not rustc:
        raise RuntimeError("Install cargo and rustc before running setup")
    compiler = run([rustc, "-vV"], timeout=10)
    target = next(line.removeprefix("host: ") for line in compiler.splitlines() if line.startswith("host: "))
    cpu = "x86-64" if args.generic else "native"
    if args.generic and not target.startswith("x86_64-"):
        raise ValueError("--generic targets the x86-64 Dusky ISO")
    flags = f"-C target-cpu={cpu}"
    with (PROJECT / "Cargo.toml").open("rb") as stream:
        configuration = tomllib.load(stream)
    version = configuration["package"]["version"]
    release_profile = configuration["profile"]["release"]
    expected = {"recipe": RECIPE, "version": version, "target": target,
                "rustc": compiler, "rustflags": flags, "source_sha256": source_digest(),
                "cpu_signature": None if args.generic else cpu_signature(),
                "release_profile": release_profile}
    output_dir = (args.output_dir or Path.home() / ".local/share/dusky/dusky_network").resolve()
    if output_dir.is_relative_to(PROJECT):
        raise ValueError("Build output must be outside the source directory")
    binary = output_dir / "dusky-network"
    manifest_path = output_dir / "binary_manifest.json"
    if not args.force_rebuild and not (args.test or args.clippy or args.check):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (isinstance(manifest, dict)
                    and all(manifest.get(key) == value for key, value in expected.items())
                    and manifest.get("binary_sha256") == digest(binary)):
                if run([str(binary), "--version"], timeout=10) == f"dusky-network {version}":
                    print(f"[OK] Verified existing {cpu} build: {binary}")
                    return
        except (OSError, ValueError, RuntimeError):
            pass

    @build_support.temporary_build("dusky-network-build-", artifact_bytes=512 * 1024 ** 2,
                     compiler_bytes=512 * 1024 ** 2, max_jobs=1)
    def compile_project(*, workspace: build_support.Workspace) -> None:
        scratch = workspace.path
        env = os.environ.copy()
        env.pop("CARGO_ENCODED_RUSTFLAGS", None)
        env.update(RUSTFLAGS=flags, CARGO_TARGET_DIR=str(scratch / "target"),
                   CARGO_BUILD_BUILD_DIR=str(scratch / "target"), TMPDIR=str(scratch),
                   CARGO_NET_OFFLINE="true", RUSTUP_AUTO_INSTALL="0", RUSTC=rustc,
                   RUSTC_WRAPPER="", RUSTC_WORKSPACE_WRAPPER="",
                   CARGO_INCREMENTAL="0")
        # Inherited profile overrides must not silently contradict the manifest.
        for key in tuple(env):
            if key.startswith("CARGO_PROFILE_RELEASE_"):
                del env[key]
        for key, value in release_profile.items():
            env["CARGO_PROFILE_RELEASE_" + key.upper().replace("-", "_")] = (
                str(value).lower() if isinstance(value, bool) else str(value))
        # A single dependency-free crate needs one compiler, regardless of CPU count.
        common = ["--frozen", "--target", target, "--jobs", "1"]
        if args.test:
            command = [cargo, "test", "--release", *common]
        elif args.clippy:
            command = [cargo, "clippy", *common, "--all-targets", "--", "-D", "warnings"]
        elif args.check:
            command = [cargo, "check", *common]
        else:
            command = [cargo, "build", "--release", *common]
        print(f"[INFO] {cpu} {command[1]} in {workspace.storage}; {flags}", flush=True)
        try:
            print(run(command, env=env))
        except RuntimeError as error:
            workspace.retry_on_disk(1, str(error))
            raise
        if args.test or args.clippy or args.check:
            return
        built = scratch / "target" / target / "release/dusky-network"
        if run([str(built), "--version"], timeout=10) != f"dusky-network {version}":
            raise RuntimeError("Built executable failed its version smoke test")
        if expected["source_sha256"] != source_digest():
            raise RuntimeError("Sources changed during build; rerun setup")
        expected["target_features"] = sorted(line for line in run(
            [rustc, "--print", "cfg", "-C", f"target-cpu={cpu}"], timeout=10).splitlines()
            if line.startswith("target_feature="))
        binary.parent.mkdir(parents=True, exist_ok=True)
        staged_binary = binary.with_suffix(f".{os.getpid()}.tmp")
        staged_manifest = manifest_path.with_suffix(f".{os.getpid()}.tmp")
        try:
            shutil.copyfile(built, staged_binary)
            staged_binary.chmod(0o755)
            expected["binary_sha256"] = digest(staged_binary)
            staged_manifest.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
            os.replace(staged_binary, binary)
            os.replace(staged_manifest, manifest_path)
        finally:
            staged_binary.unlink(missing_ok=True)
            staged_manifest.unlink(missing_ok=True)
        print(f"[OK] Built {binary}; no services or Waybar configuration changed")

    compile_project()


def verified(binary: Path, manifest_path: Path, *, native: bool) -> dict | None:
    """Check target/CPU/hash before executing an existing build."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("target") != TARGET
                or manifest.get("rustflags") != f"-C target-cpu={'native' if native else 'x86-64'}"
                or manifest.get("cpu_signature") != (cpu_signature() if native else None)
                or manifest.get("binary_sha256") != digest(binary)):
            return None
        if run([str(binary), "--version"], timeout=10) != f"dusky-network {manifest.get('version')}":
            return None
        return manifest
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return None


def current(manifest: dict | None, configuration: dict) -> bool:
    return bool(manifest and manifest.get("recipe") == RECIPE
                and manifest.get("version") == configuration["package"]["version"]
                and manifest.get("source_sha256") == source_digest()
                and manifest.get("release_profile") == configuration["profile"]["release"])


def link(target: Path, destination: Path) -> None:
    if destination.is_symlink() and destination.readlink() == target:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.symlink_to(target)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def install(install_dir: Path, launcher: Path, force: bool) -> bool:
    """Return whether the installed executable changed; preserve working fallback."""
    binary = install_dir / "dusky-network"
    manifest_path = install_dir / "binary_manifest.json"
    with (PROJECT / "Cargo.toml").open("rb") as stream:
        configuration = tomllib.load(stream)
    old_native = verified(binary, manifest_path, native=True)
    packaged = verified(SYSTEM_BINARY, SYSTEM_MANIFEST, native=False)
    try:
        before = digest(binary)
    except OSError:
        before = None
    native_current = current(old_native, configuration)
    # A compiler update invalidates a native build when a toolchain is available.
    rustc = shutil.which("rustc")
    if native_current and rustc:
        try:
            native_current = old_native.get("rustc") == run([rustc, "-vV"], timeout=10)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            native_current = False
    selected = native_current and not force
    if selected:
        print("[OK] Using verified native network meter")
    elif not force and not old_native and current(packaged, configuration):
        link(SYSTEM_BINARY, binary)
        manifest_path.unlink(missing_ok=True)
        selected = True
        print("[OK] Using matching generic ISO package; no compilation needed")
    if not selected:
        try:
            build(argparse.Namespace(force_rebuild=True, generic=False, test=False,
                                     clippy=False, check=False, output_dir=install_dir))
            selected = True
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
            print(f"[WARN] Native network build failed: {error}", file=sys.stderr)
    if not selected and old_native and verified(binary, manifest_path, native=True):
        selected = True
        print("[WARN] Keeping previous verified native build; rerun setup to update", file=sys.stderr)
    if not selected and packaged:
        link(SYSTEM_BINARY, binary)
        manifest_path.unlink(missing_ok=True)
        selected = True
        print("[WARN] Using older ISO package; rerun setup to update", file=sys.stderr)
    if not selected:
        raise RuntimeError("No verified native or packaged network meter is available")
    link(binary, launcher)
    print(f"[OK] Dusky Network ready at {launcher}")
    return before != digest(binary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--generic", action="store_true", help="Build baseline x86-64 for the ISO")
    parser.add_argument("--output-dir", type=Path, help="Required generic-build destination outside the source tree")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--test", action="store_true")
    group.add_argument("--clippy", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if platform.machine() != "x86_64":
        parser.error("The Dusky ISO supports x86-64 only")
    if args.generic and args.output_dir is None:
        parser.error("--generic requires --output-dir outside the source tree")
    if args.output_dir is not None and not args.generic:
        parser.error("--output-dir is only used for generic ISO builds")
    install_dir = Path.home() / ".local/share/dusky/dusky_network"
    destination = args.output_dir.resolve() if args.generic else install_dir
    if destination.resolve().is_relative_to(PROJECT):
        parser.error("Build output must be outside the source directory")
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.generic or args.test or args.clippy or args.check:
            build(args)
            return
        changed = install(install_dir, Path.home() / ".local/bin/dusky-network", args.force_rebuild)
        # Step 130 installs/enables the unit. Refresh an already active meter
        # after edits without starting it in an installer session.
        if changed and shutil.which("systemctl"):
            result = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "dusky_network.service"],
                                    stdin=subprocess.DEVNULL, timeout=10, check=False)
            if result.returncode == 0:
                run(["systemctl", "--user", "restart", "dusky_network.service"], timeout=30)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"[ERR] {error}", file=sys.stderr)
        sys.exit(1)
