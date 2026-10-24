#!/usr/bin/env python3
"""Compile a generic Rust ISO executable with temporary, memory-budgeted storage."""

import argparse
import os
import shutil
import signal
import subprocess
import sys
from importlib import import_module
from pathlib import Path

sys.dont_write_bytecode = True
# makepkg places both declared local sources in srcdir; keep that location even
# when the build adapter itself is a symlink into the recipe directory.
sys.path.insert(0, str(Path(__file__).absolute().parent))
shared = import_module("147_rust_compiles_shared_NOT_STANDALONE")
TARGET = "x86_64-unknown-linux-gnu"


def run(command: list[str], project: Path, env: dict[str, str], *, timeout: int) -> None:
    process = subprocess.Popen(
        command, cwd=project, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", start_new_session=True,
        pass_fds=shared.build_lock_fds(),
    )
    try:
        output, _ = process.communicate(timeout=timeout)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, command, output=output)
        print(output, end="", flush=True)
    except BaseException:
        # Cargo can fail while its compiler descendants are still alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise


@shared.temporary_build("dusky-iso-build-")
def build(project: Path, output: Path, *, workspace: shared.Workspace) -> None:
    cargo = shutil.which("cargo")
    if cargo is None:
        raise RuntimeError("Cargo is required to build this package")
    env = os.environ.copy()
    env.pop("CARGO_ENCODED_RUSTFLAGS", None)
    env.update(
        RUSTFLAGS="-C target-cpu=x86-64",
        CFLAGS="-march=x86-64 -mtune=generic -O2",
        CXXFLAGS="-march=x86-64 -mtune=generic -O2",
        CPPFLAGS="", RUSTUP_AUTO_INSTALL="0", CARGO_INCREMENTAL="0",
        CARGO_TARGET_DIR=str(workspace.path / "target"),
        CARGO_BUILD_BUILD_DIR=str(workspace.path / "target"),
        TMPDIR=str(workspace.path),
    )
    # The ISO factory can fetch missing crates; frozen compilation then uses
    # only that shared dependency cache. Installed systems remain offline.
    run([cargo, "fetch", "--locked", "--target", TARGET], project, env, timeout=3600)
    env["CARGO_NET_OFFLINE"] = "true"
    try:
        run([cargo, "build", "--release", "--frozen", "--target", TARGET,
             "--jobs", str(workspace.jobs), "--bin", output.name], project, env, timeout=3600)
    except subprocess.CalledProcessError as error:
        workspace.retry_on_disk(error.returncode, error.output)
        raise
    built = workspace.path / "target" / TARGET / "release" / output.name
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built, output)
    except OSError as error:
        # Disk output exhaustion is not a reason to repeat compilation on disk.
        raise RuntimeError(f"Could not stage package executable: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build(args.project.resolve(), args.output.resolve())


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda signum, _frame: sys.exit(128 + signum))
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.output, file=sys.stderr, end="")
        sys.exit(error.returncode if error.returncode > 0 else 1)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"[ERR] ISO Rust build failed: {error}", file=sys.stderr)
        sys.exit(1)
