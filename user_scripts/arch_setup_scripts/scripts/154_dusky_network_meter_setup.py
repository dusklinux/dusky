#!/usr/bin/env python3
"""Install a matching ISO network meter, or build it for this CPU."""

from functools import cache
import json
import runpy
import sys
from importlib import import_module
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
build_support = import_module("147_rust_compiles_shared_NOT_STANDALONE")

SYSTEM_SOURCE_DIGEST = Path("/usr/share/dusky_network/source.sha256")
SYSTEM_MANIFEST = Path("/usr/share/dusky_network/binary_manifest.json")
DEFAULT_PROJECT = Path(__file__).resolve().parents[2] / "dusky/dusky_network"


@cache
def _network_setup_ns(project: Path) -> dict:
    return runpy.run_path(str(project / "setup.py"), run_name="dusky_network_setup")


def source_digest(project: Path = DEFAULT_PROJECT) -> str:
    ns = _network_setup_ns(project)
    fn = ns.get("source_digest")
    if callable(fn):
        try:
            return fn(project)
        except TypeError:
            return fn()
    return build_support.source_digest(
        project,
        recipe=ns.get("RECIPE", ns.get("BUILD_RECIPE", "dusky_network-tmpfs-v2")),
        include_fonts=False,
    )


def packaged_source_matches(project: Path = DEFAULT_PROJECT) -> bool:
    try:
        current = source_digest(project)
        if SYSTEM_SOURCE_DIGEST.is_file():
            return SYSTEM_SOURCE_DIGEST.read_text(encoding="utf-8").strip() == current
        if SYSTEM_MANIFEST.is_file():
            data = json.loads(SYSTEM_MANIFEST.read_text(encoding="utf-8"))
            return isinstance(data, dict) and data.get("source_sha256") == current
    except (OSError, ValueError):
        pass
    return False


if __name__ == "__main__":
    runpy.run_path(str(DEFAULT_PROJECT / "setup.py"), run_name="__main__")
