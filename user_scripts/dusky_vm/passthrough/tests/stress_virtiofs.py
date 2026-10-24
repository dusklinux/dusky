"""Exercise a running VM's RAM-backed VirtioFS export using isolated scratch files.

Run with Python 3.15: python3 -B tests/stress_virtiofs.py DOMAIN
SSH and --configure must already work. No VM restart or user-file modification.
"""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linux import deploy_vm as vm


LIB = '''
import concurrent.futures, hashlib, json, os, time
from pathlib import Path
def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
def large(root, name, mib):
    path = root / name
    checksum = hashlib.sha256()
    with path.open('wb') as stream:
        for _ in range(mib):
            block = os.urandom(1 << 20)
            stream.write(block)
            checksum.update(block)
        stream.flush()
        os.fsync(stream.fileno())
    return checksum.hexdigest()
def small(root, side, worker, count):
    manifest = {}
    for index in range(count):
        name = f'{side}-{worker}-{index:04d} spaces λ नमस्ते.bin'
        data = os.urandom(32768)
        path = root / name
        temporary = path.with_suffix('.partial')
        with temporary.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        manifest[name] = hashlib.sha256(data).hexdigest()
    return manifest
def parallel_small(root, side, workers, count):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda n: small(root, side, n, count), range(workers))
        return {name: value for result in results for name, value in result.items()}
def atomic_write(root, name, updates):
    path = root / name
    for version in range(updates):
        body = os.urandom(65536 - 40)
        data = version.to_bytes(8, 'little') + hashlib.sha256(body).digest() + body
        temporary = path.with_suffix('.partial')
        with temporary.open('wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
def atomic_read(path):
    data = path.read_bytes()
    assert len(data) == 65536, ('torn size', len(data))
    assert hashlib.sha256(data[40:]).digest() == data[8:40], 'torn content'
'''

LOCAL = {}
exec(LIB, LOCAL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument("--large-mib", type=int, default=256)
    parser.add_argument("--files-per-worker", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--atomic-updates", type=int, default=300)
    args = parser.parse_args()
    assets, export = vm.domain_config(args.domain)
    if vm.filesystem_type(export) not in {"tmpfs", "ramfs"}:
        parser.error("This stress suite requires a RAM-backed host export.")
    config = json.loads((assets / "connection.json").read_text())
    ssh = vm.ssh_command(config)
    root = Path(tempfile.mkdtemp(prefix=".dusky-share-stress-", dir=export))
    guest = Path(config["share"]) / root.name
    report = {"domain": args.domain, "export": str(export), "checks": {}}
    started = time.monotonic()

    def script(code):
        return LIB + f"\nroot = Path({str(guest)!r})\n" + code

    def remote(code, timeout=90):
        result = subprocess.run([*ssh, "python3", "-u", "-"], input=script(code),
                                capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return json.loads(result.stdout)

    def record(name, detail):
        report["checks"][name] = detail
        print(f"PASS {name}: {detail}", flush=True)

    try:
        checksum = LOCAL["large"](root, "host-large.bin", args.large_mib)
        assert remote("print(json.dumps(digest(root / 'host-large.bin'))) ") == checksum
        checksum = remote(f"print(json.dumps(large(root, 'guest-large.bin', {args.large_mib})))")
        assert LOCAL["digest"](root / "guest-large.bin") == checksum
        record("large_files", f"{args.large_mib} MiB per direction, SHA-256 matched")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            guest_work = pool.submit(remote, f"print(json.dumps(parallel_small(root, 'guest', {args.workers}, {args.files_per_worker})))")
            host_files = LOCAL["parallel_small"](root, "host", args.workers, args.files_per_worker)
            guest_files = guest_work.result()
        checked = remote(f"manifest = {host_files!r}\nprint(json.dumps(all(digest(root / name) == checksum for name, checksum in manifest.items())))")
        assert checked
        assert all(LOCAL["digest"](root / name) == checksum for name, checksum in guest_files.items())
        record("concurrent_files", f"{len(host_files) + len(guest_files)} files, {args.workers * 2} writers, SHA-256 matched")

        LOCAL["atomic_write"](root, "host-atomic.bin", 1)
        code = '''
print('READY', flush=True)
reads = 0
deadline = time.monotonic() + 60
while not (root / 'host-atomic-done').exists():
    atomic_read(root / 'host-atomic.bin')
    reads += 1
    assert time.monotonic() < deadline, 'writer timeout'
atomic_read(root / 'host-atomic.bin')
print(json.dumps(reads + 1), flush=True)
'''
        reader = subprocess.Popen([*ssh, "python3", "-u", "-"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        reader.stdin.write(script(code))
        reader.stdin.close()
        assert reader.stdout.readline().strip() == "READY"
        LOCAL["atomic_write"](root, "host-atomic.bin", args.atomic_updates)
        (root / "host-atomic-done").touch()
        output = reader.stdout.read()
        assert reader.wait(timeout=60) == 0, reader.stderr.read()
        guest_reads = json.loads(output)
        LOCAL["atomic_write"](root, "guest-atomic.bin", 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            writer = pool.submit(remote, f"atomic_write(root, 'guest-atomic.bin', {args.atomic_updates})\nprint(json.dumps(True))")
            host_reads = 0
            while not writer.done():
                LOCAL["atomic_read"](root / "guest-atomic.bin")
                host_reads += 1
            assert writer.result()
        record("atomic_replace", {"replacements": args.atomic_updates * 2, "guest_reads": guest_reads, "host_reads": host_reads})

        sparse = root / "sparse.bin"
        with sparse.open("wb") as stream:
            stream.truncate(2 << 30)
            stream.seek(0)
            stream.write(b"begin")
            stream.seek((2 << 30) - 3)
            stream.write(b"end")
        assert remote('''
path = root / 'sparse.bin'
assert path.stat().st_size == 2 << 30
with path.open('rb') as stream:
    assert stream.read(5) == b'begin'
    stream.seek(1 << 30)
    assert stream.read(4096) == bytes(4096)
    stream.seek(-3, 2)
    assert stream.read() == b'end'
with path.open('r+b') as stream:
    stream.truncate(1 << 30)
print(json.dumps(True))
''')
        assert sparse.stat().st_size == 1 << 30
        assert sparse.stat().st_blocks * 512 < 1 << 20
        record("sparse_and_truncate", "2 GiB sparse file, holes verified, guest truncation visible on host")

        (root / "Case λ.txt").write_text("case-sensitive")
        (root / "case λ.txt").write_text("different")
        os.link(root / "Case λ.txt", root / "hardlink")
        (root / "symlink").symlink_to("Case λ.txt")
        assert remote('''
assert (root / 'symlink').read_text() == 'case-sensitive'
assert (root / 'hardlink').stat().st_ino == (root / 'Case λ.txt').stat().st_ino
assert (root / 'case λ.txt').read_text() == 'different'
(root / 'hardlink').chmod(0o640)
(root / 'case λ.txt').rename(root / 'renamed λ.txt')
print(json.dumps(True))
''')
        assert (root / "Case λ.txt").stat().st_mode & 0o777 == 0o640
        assert (root / "renamed λ.txt").read_text() == "different"
        record("metadata", "Unicode/spaces, case distinction, hardlinks, symlinks, rename and chmod")

        assert remote(f"names = {list(host_files)!r}\nfor name in names:\n    (root / name).unlink()\nprint(json.dumps(True))")
        for name in guest_files:
            (root / name).unlink()
        assert not any((root / name).exists() for name in host_files)
        assert remote(f"print(json.dumps(not any((root / name).exists() for name in {list(guest_files)!r})))")
        record("cross_deletion", f"{len(host_files) + len(guest_files)} files removed from the opposite side")
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(report, indent=2))
        return report
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    main()
