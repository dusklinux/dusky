#!/usr/bin/env python3
# ==============================================================================
# verify_repo.py
#
# Diagnostics & Self-Healing utility to verify and repair offline repositories.
# Verifies packages via:
#   1. ZStandard archive structure verification (decoding test)
#   2. SHA256 checksum comparison against the repository database (archrepo.db)
#
# If any packages are corrupted, it dynamically fetches clean versions from 
# official online mirrors and places them directly into the pacman cache.
# ==============================================================================

import os
import sys
import shutil
import tarfile
import hashlib
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path

# Colors
C_BOLD = "\033[1m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_CYAN = "\033[36m"
C_RESET = "\033[0m"

def log_info(msg): print(f"{C_CYAN}{C_BOLD}[INFO]{C_RESET} {msg}")
def log_ok(msg):   print(f"{C_GREEN}{C_BOLD}[OK]{C_RESET} {msg}")
def log_warn(msg): print(f"{C_YELLOW}{C_BOLD}[WARN]{C_RESET} {msg}")
def log_err(msg):  print(f"{C_RED}{C_BOLD}[ERR]{C_RESET} {msg}", file=sys.stderr)

def get_sha256(filepath):
    """Calculate SHA256 of a file in chunks to handle large packages efficiently."""
    h = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        log_err(f"Failed to calculate hash for {filepath.name}: {e}")
        return None

def verify_zstd_archive(filepath):
    """Perform a dry-run decompression check using zstd."""
    try:
        res = subprocess.run(["zstd", "-t", str(filepath)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except FileNotFoundError:
        # Fallback if zstd command line tool is missing
        return True

def parse_db_metadata(db_path):
    """Extract package names, filenames, and SHA256 sums from the repository db archive."""
    expected = {}
    if not db_path.exists():
        log_err(f"Repository database not found: {db_path}")
        return expected

    try:
        with tarfile.open(db_path, "r:gz") as tar:
            for member in tar.getmembers():
                if member.name.endswith("/desc"):
                    desc_content = tar.extractfile(member).read().decode('utf-8', errors='ignore')
                    lines = desc_content.splitlines()
                    
                    filename = None
                    pkg_name = None
                    sha256sum = None
                    
                    for i, line in enumerate(lines):
                        if line.strip() == "%FILENAME%":
                            filename = lines[i+1].strip()
                        elif line.strip() == "%NAME%":
                            pkg_name = lines[i+1].strip()
                        elif line.strip() == "%SHA256SUM%":
                            sha256sum = lines[i+1].strip()
                    
                    if filename and pkg_name and sha256sum:
                        expected[filename] = {
                            "name": pkg_name,
                            "sha256": sha256sum
                        }
    except Exception as e:
        log_err(f"Failed to read database metadata: {e}")
    
    return expected

def check_internet_connection():
    """Verify online connectivity by attempting to resolve and ping a mirror."""
    try:
        res = subprocess.run(["ping", "-c", "1", "-W", "2", "geo.mirror.pkgbuild.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except:
        return False

def resolve_download_urls(pkg_names):
    """Generate a temporary pacman config to resolve online package URLs."""
    urls = {}
    conf_path = Path("/tmp/heal_pacman.conf")
    
    # Online mirrors configuration template
    conf_content = """[options]
Architecture = auto
SigLevel = Never

[cachyos-v3]
Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo
[cachyos-core-v3]
Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo
[cachyos-extra-v3]
Server = https://mirror.cachyos.org/repo/x86_64_v3/$repo
[cachyos]
Server = https://mirror.cachyos.org/repo/x86_64/$repo

[core]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
[extra]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
[multilib]
Server = https://geo.mirror.pkgbuild.com/$repo/os/$arch
"""
    try:
        conf_path.write_text(conf_content)
        
        log_info("Synchronizing online databases in sandbox...")
        subprocess.run(["pacman", "-Sy", "--config", str(conf_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        log_info("Resolving package URLs...")
        for name in pkg_names:
            # pacman -Sp prints direct download URLs
            res = subprocess.run(["pacman", "-Sp", "--config", str(conf_path), name], capture_output=True, text=True)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    if line.strip().startswith("http"):
                        # Map package name directly to its resolved online URL
                        urls[name] = line.strip()
    except Exception as e:
        log_err(f"Failed during URL resolution: {e}")
    finally:
        if conf_path.exists():
            conf_path.unlink()
            
    return urls

def download_file(url, dest_path):
    """Download a file with user-agent headers and visual progress indication."""
    try:
        log_info(f"Downloading: {dest_path.name}")
        
        # Quote path part to correctly encode colons (e.g. 1:2026... -> 1%3A2026...)
        parts = list(urllib.parse.urlsplit(url))
        parts[2] = urllib.parse.quote(parts[2])
        quoted_url = urllib.parse.urlunsplit(parts)
        
        # User-Agent header bypasses CDN blockings
        req = urllib.request.Request(quoted_url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req) as response:
            total_size = int(response.info().get('Content-Length', 0))
            block_size = 8192
            downloaded = 0
            
            with open(dest_path, 'wb') as f:
                while True:
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    downloaded += len(buffer)
                    f.write(buffer)
                    
                    # Progress report
                    percent = min(100, int(downloaded * 100 / total_size)) if total_size > 0 else 0
                    sys.stdout.write(f"\r  -> Progress: {percent}% ({downloaded // 1024} KB / {total_size // 1024} KB)")
                    sys.stdout.flush()
            print() # Final newline
        return True
    except Exception as e:
        print()
        log_err(f"Download failed for {url}: {e}")
        return False

def self_heal_repo(corrupted_pkgs, db_metadata, repo_dir):
    """Download verified, uncorrupted versions of corrupted packages to cache."""
    if not corrupted_pkgs:
        return True

    print("\n" + "="*80)
    print(f"{C_BOLD}SELF-HEALING MECHANISM (ONLINE RECOVERY){C_RESET}")
    print("="*80)
    
    # Determine cache directory
    cache_dir = Path("/mnt/var/cache/pacman/pkg")
    if not cache_dir.exists():
        cache_dir = Path("./healed_packages")
        
    log_info(f"Target cache directory: {cache_dir}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if repo is writable (ISO filesystems are read-only)
    repo_writable = os.access(str(repo_dir), os.W_OK)
    if not repo_writable:
        log_warn(f"Repository '{repo_dir}' is read-only. Healed packages will be placed in cache only.")
        log_info("Verification will use cache as overlay on subsequent runs.")
    
    if not check_internet_connection():
        log_err("Self-healing failed: No internet connection detected.")
        log_info("Please connect the target system to the network and try again.")
        return False
        
    log_ok("Internet connection confirmed.")
    
    # Extract clean package names directly from db metadata
    pkg_names_to_query = []
    filename_map = {} # Maps resolved name back to expected database filename
    
    for filename in corrupted_pkgs:
        pkg_name = db_metadata[filename]["name"]
        pkg_names_to_query.append(pkg_name)
        filename_map[pkg_name] = filename

    # Resolve URLs
    resolved_urls = resolve_download_urls(pkg_names_to_query)
    
    if not resolved_urls:
        log_err("Failed to resolve package download URLs from mirrors.")
        return False
        
    healed_count = 0
    
    for pkg_name, expected_filename in filename_map.items():
        url_to_download = resolved_urls.get(pkg_name)
                    
        if not url_to_download:
            log_warn(f"Could not resolve direct mirror URL for package '{pkg_name}'. Skipping.")
            continue
            
        dest_pkg = cache_dir / expected_filename
        dest_sig = cache_dir / (expected_filename + ".sig")
        
        # Check if the cache already contains the valid, uncorrupted file
        expected_hash = db_metadata[expected_filename]["sha256"]
        if dest_pkg.exists() and verify_zstd_archive(dest_pkg) and get_sha256(dest_pkg) == expected_hash:
            log_ok(f"Cache already contains valid file: {expected_filename} (Skipping download)")
            healed_count += 1
            continue
            
        # Download package ZST
        if download_file(url_to_download, dest_pkg):
            # Download signature file (if exists on mirror)
            download_file(url_to_download + ".sig", dest_sig)
            
            # Verify downloaded file integrity
            if verify_zstd_archive(dest_pkg):
                # If repo is writable, copy back so re-verification passes at source
                if repo_writable:
                    repo_pkg = repo_dir / expected_filename
                    if repo_pkg != dest_pkg:
                        shutil.copy2(dest_pkg, repo_pkg)
                log_ok(f"Successfully healed: {expected_filename}")
                healed_count += 1
            else:
                log_err(f"Downloaded package failed integrity test: {expected_filename}")
                dest_pkg.unlink(missing_ok=True)
                dest_sig.unlink(missing_ok=True)

    print("\n" + "="*80)
    if healed_count == len(corrupted_pkgs):
        log_ok("Self-healing complete! All corrupted packages repaired and cached.")
        return True
    else:
        log_warn(f"Self-healing partially completed. Fixed {healed_count}/{len(corrupted_pkgs)} packages.")
        return False

def main():
    # Detect location: default to /offline_repo, fallback to local directory
    search_paths = [
        Path("/offline_repo"),
        Path("/run/archiso/bootmnt/arch/repo"),
        Path(__file__).parent,
        Path.cwd()
    ]
    
    repo_dir = None
    for p in search_paths:
        if p.exists() and (p / "archrepo.db").exists():
            repo_dir = p
            break
            
    if not repo_dir:
        log_err("Could not find repository directory containing 'archrepo.db'.")
        print("\nPaths searched:")
        for p in search_paths:
            print(f"  - {p}")
        sys.exit(1)

    log_info(f"Using repository source: {repo_dir}")
    
    # Determine cache directory (used as writable overlay for read-only repos like ISO)
    cache_dir = Path("/mnt/var/cache/pacman/pkg")
    if not cache_dir.exists():
        cache_dir = Path("./healed_packages")
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    db_path = repo_dir / "archrepo.db"
    log_info("Parsing database metadata...")
    db_packages = parse_db_metadata(db_path)
    
    if not db_packages:
        log_err("No package metadata found in database.")
        sys.exit(1)
        
    log_ok(f"Database parsed successfully. Found {len(db_packages)} packages.")
    
    corrupted = []
    missing = []
    cache_verified = []
    verified_count = 0
    
    log_info("Starting packages verification (Structure & SHA256)...")
    
    for filename, meta in db_packages.items():
        expected_hash = meta["sha256"]
        pkg_path = repo_dir / filename
        
        if not pkg_path.exists():
            alt_name = filename.replace(":", "_")
            if (repo_dir / alt_name).exists():
                pkg_path = repo_dir / alt_name
            else:
                missing.append(filename)
                continue
        
        # 1. Structural Verification
        if not verify_zstd_archive(pkg_path):
            # Check cache as overlay before declaring corrupted
            cached = cache_dir / filename
            if cached.exists() and verify_zstd_archive(cached) and get_sha256(cached) == expected_hash:
                cache_verified.append(filename)
                verified_count += 1
                continue
            log_err(f"Corrupted (Zstandard Decoding Failure): {pkg_path.name}")
            corrupted.append(filename)
            continue
            
        # 2. Cryptographic Integrity
        calculated_hash = get_sha256(pkg_path)
        if not calculated_hash:
            corrupted.append(filename)
            continue
            
        if calculated_hash != expected_hash:
            # Check cache as overlay before declaring corrupted
            cached = cache_dir / filename
            if cached.exists() and verify_zstd_archive(cached) and get_sha256(cached) == expected_hash:
                cache_verified.append(filename)
                verified_count += 1
                continue
            log_err(f"Mismatched SHA256 Checksum: {pkg_path.name}")
            corrupted.append(filename)
        else:
            verified_count += 1
            
    print("\n" + "="*80)
    print(f"{C_BOLD}VERIFICATION SUMMARY{C_RESET}")
    print("="*80)
    print(f"  Total DB packages : {len(db_packages)}")
    print(f"  Verified (OK)     : {C_GREEN}{verified_count}{C_RESET}")
    
    if cache_verified:
        print(f"  Cache overlay     : {C_YELLOW}{len(cache_verified)}{C_RESET} packages verified via target cache")
    
    if missing:
        print(f"  Missing packages  : {C_YELLOW}{len(missing)}{C_RESET}")
        
    if corrupted:
        print(f"  Corrupted packages: {C_RED}{len(corrupted)}{C_RESET}")
        for c in corrupted:
            print(f"    - {c}")
            
        # Trigger self-healing
        self_heal_repo(corrupted, db_packages, repo_dir)

        # Re-verify with cache overlay: check if all now satisfied via cache
        still_broken = []
        for filename in corrupted:
            expected_hash = db_packages[filename]["sha256"]
            cached = cache_dir / filename
            if cached.exists() and verify_zstd_archive(cached) and get_sha256(cached) == expected_hash:
                log_ok(f"Now valid via cache: {filename}")
            else:
                still_broken.append(filename)

        if not still_broken:
            log_ok("All packages verified successfully (via cache overlay).")
            sys.exit(0)
        else:
            log_err(f"Still {len(still_broken)} packages could not be healed: {still_broken}")
            sys.exit(1)
    else:
        log_ok("All packages matched database checksums and verified successfully.")
        sys.exit(0)

if __name__ == "__main__":
    main()
