#!/usr/bin/env python3
"""repack_package_digests.py: Refresh /usr/share/<pkg>/source.sha256 inside precompiled
Dusky .pkg.tar.zst archives in tmpfs while preserving binary payloads and makepkg metadata.
"""

from functools import cmp_to_key
from importlib import import_module
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

DUSKY_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = DUSKY_DIR.parent / "arch_setup_scripts" / "scripts"
REPO_DIR = Path("/srv/offline-repo/aur")
TMP_BASE = Path("/mnt/zram1") if Path("/mnt/zram1").is_dir() else Path("/tmp")

sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPTS_DIR))

PACKAGE_MODULES: dict[str, tuple[str, Path]] = {
    "dusky_snap": ("150_dusky_snap_setup", DUSKY_DIR / "dusky_snap"),
    "dusky_papers": ("151_dusky_papers_setup", DUSKY_DIR / "dusky_papers"),
    "dusky_tray": ("152_dusky_tray_setup", DUSKY_DIR / "dusky_tray"),
    "dusky_center": ("153_dusky_center_setup", DUSKY_DIR / "dusky_center"),
    "dusky_overview": ("153_dusky_overview_setup", DUSKY_DIR / "dusky_overview"),
    "dusky_network": ("154_dusky_network_meter_setup", DUSKY_DIR / "dusky_network"),
    "dusky_casts": ("154_dusky_casts_setup", DUSKY_DIR / "dusky_casts"),
}


def repack_one(pkg_archive: Path, pkgname: str, new_digest: str, *, force: bool = False) -> bool:
    with tempfile.TemporaryDirectory(prefix=f"repack-{pkgname}-", dir=TMP_BASE) as tmp:
        work = Path(tmp) / "pkg"
        work.mkdir()

        subprocess.run(
            ["bsdtar", "-xpf", str(pkg_archive), "-C", str(work)],
            check=True,
        )

        digest_file = work / "usr/share" / pkgname / "source.sha256"
        digest_file.parent.mkdir(parents=True, exist_ok=True)
        old_digest = digest_file.read_text(encoding="utf-8").strip() if digest_file.is_file() else ""
        if old_digest == new_digest and not force:
            print(f"[skip]   {pkg_archive.name}: source.sha256 already matches ({new_digest[:12]}...)")
            return False

        digest_file.write_text(f"{new_digest}\n", encoding="utf-8")
        digest_file.chmod(0o644)

        # Preserve .BUILDINFO builddate as mtime for deterministic .MTREE timestamps
        buildinfo = work / ".BUILDINFO"
        if buildinfo.is_file():
            m = re.search(r"^builddate\s*=\s*(\d+)", buildinfo.read_text(encoding="utf-8"), re.M)
            if m:
                ts = int(m.group(1))
                os.utime(digest_file, (ts, ts))

        # Recompute installed size in .PKGINFO
        pkginfo = work / ".PKGINFO"
        if pkginfo.is_file():
            total_size = sum(
                p.stat().st_size
                for p in work.rglob("*")
                if p.is_file() and not p.name.startswith(".")
            )
            updated = re.sub(
                r"^size\s*=\s*\d+",
                f"size = {total_size}",
                pkginfo.read_text(encoding="utf-8"),
                flags=re.M,
            )
            pkginfo.write_text(updated, encoding="utf-8")

        # Collect payload files in sorted order (excluding metadata dotfiles)
        rel_files = sorted(
            p.relative_to(work).as_posix()
            for p in work.rglob("*")
            if not p.relative_to(work).parts[0].startswith(".")
        )

        # Regenerate gzipped .MTREE with makepkg's exact options and root:root ownership
        mtree_path = work / ".MTREE"
        mtree_path.unlink(missing_ok=True)
        mtree_options = "!all,use-set,type,uid,gid,mode,time,size,md5,sha256,link"
        subprocess.run(
            [
                "bsdtar",
                "-czf",
                ".MTREE",
                "--format=mtree",
                f"--options={mtree_options}",
                "--uid", "0",
                "--gid", "0",
                "--uname", "root",
                "--gname", "root",
                *rel_files,
            ],
            cwd=work,
            check=True,
        )

        # Pack metadata dotfiles first, then payload, compressed with zstd -20
        ordered_entries = [
            name for name in (".PKGINFO", ".BUILDINFO", ".MTREE", ".INSTALL") if (work / name).exists()
        ] + rel_files

        staged_pkg = Path(tmp) / f".{pkg_archive.name}.tmp"
        tar_proc = subprocess.Popen(
            [
                "bsdtar",
                "-cf",
                "-",
                "--no-recursion",
                "--uid", "0",
                "--gid", "0",
                "--uname", "root",
                "--gname", "root",
                *ordered_entries,
            ],
            cwd=work,
            stdout=subprocess.PIPE,
        )
        assert tar_proc.stdout is not None
        with staged_pkg.open("wb") as out:
            zstd_proc = subprocess.run(
                ["zstd", "-c", "-T0", "--ultra", "-20"],
                stdin=tar_proc.stdout,
                stdout=out,
                check=True,
            )
        tar_proc.stdout.close()
        if tar_proc.wait() != 0:
            raise RuntimeError(f"bsdtar failed while packing {pkg_archive.name}")

        shutil.move(str(staged_pkg), str(pkg_archive))
        print(f"[repack] {pkg_archive.name}: updated root:root payload -> {new_digest[:12]}...")
        return True


def main() -> int:
    force = "--force" in sys.argv
    changed = 0
    for pkgname, (mod_name, proj_dir) in PACKAGE_MODULES.items():
        mod = import_module(mod_name)
        digest = mod.source_digest(proj_dir)
        candidates = list(REPO_DIR.glob(f"{pkgname}-*.pkg.tar.zst"))
        if not candidates:
            print(f"[warn]   No archive found for {pkgname} in {REPO_DIR}", file=sys.stderr)
            continue

        def _pkg_cmp(a: Path, b: Path) -> int:
            ver_a = a.name.removeprefix(f"{pkgname}-").rsplit("-", 1)[0]
            ver_b = b.name.removeprefix(f"{pkgname}-").rsplit("-", 1)[0]
            out = subprocess.run(["vercmp", ver_a, ver_b], capture_output=True, text=True, check=True)
            return int(out.stdout.strip())

        candidates.sort(key=cmp_to_key(_pkg_cmp))
        if repack_one(candidates[-1], pkgname, digest, force=force):
            changed += 1
    print(f"Done ({changed} package(s) updated).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
