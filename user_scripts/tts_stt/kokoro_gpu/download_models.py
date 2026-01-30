#!/usr/bin/env python3
"""
Download Kokoro model files if they don't exist.
"""
import os
import sys
import urllib.request
from pathlib import Path

# Paths
APP_DIR = Path.home() / "contained_apps/uv/kokoro_gpu"
MODEL_PATH = APP_DIR / "kokoro-v1.0.fp16-gpu.onnx"
VOICES_PATH = APP_DIR / "voices-v1.0.bin"

# URLs (from Kokoro project releases)
MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.fp16-gpu.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

def download_file(url: str, dest: Path) -> bool:
    """Download a file from URL to destination."""
    if dest.exists():
        print(f"✓ {dest.name} already exists")
        return True
    
    print(f"⬇ Downloading {dest.name}...")
    try:
        urllib.request.urlretrieve(url, dest)
        print(f"✓ Downloaded {dest.name}")
        return True
    except Exception as e:
        print(f"✗ Failed to download {dest.name}: {e}")
        return False

def main():
    os.makedirs(APP_DIR, exist_ok=True)
    os.chdir(APP_DIR)
    
    success = True
    success &= download_file(MODEL_URL, MODEL_PATH)
    success &= download_file(VOICES_URL, VOICES_PATH)
    
    if success:
        print("\n✓ All model files ready!")
        sys.exit(0)
    else:
        print("\n✗ Some files failed to download")
        sys.exit(1)

if __name__ == "__main__":
    main()
