"""NVIDIA recommendation; no CUDA libraries are imported into the recorder."""
import csv
import shutil
import subprocess


def detect_nvidia() -> list[dict]:
    command = shutil.which("nvidia-smi")
    if not command:
        return []
    try:
        result = subprocess.run([command,
            "--query-gpu=index,uuid,name,driver_version,memory.total,compute_cap",
            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=15)
        if result.returncode:
            return []
        cards = []
        for row in csv.reader(result.stdout.splitlines(), skipinitialspace=True):
            if len(row) != 6:
                continue
            try:
                index, uuid, name, driver, memory, capability = row
                memory = int(float(memory)); capability = float(capability)
                # CUDA 13 drops pre-Turing support. The arena needs room for
                # Parakeet plus the desktop; the actual GPU smoke test is final.
                if int(driver.split(".")[0]) >= 580 and capability >= 7.5 and memory >= 1792:
                    cards.append({"index": int(index), "uuid": uuid, "name": name,
                        "driver": driver, "memory_mib": memory, "compute_cap": capability})
            except ValueError:
                continue
        return sorted(cards, key=lambda card: card["memory_mib"], reverse=True)
    except (OSError, subprocess.SubprocessError):
        return []


def select_gpu(cards: list[dict], requested: int | None = None) -> dict | None:
    return next((card for card in cards if requested is None or card["index"] == requested), None)
