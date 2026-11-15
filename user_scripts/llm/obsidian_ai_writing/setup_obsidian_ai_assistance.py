#!/usr/bin/env python3
"""
Automated Setup & Lifecycle Manager for Local LLM Writing in Obsidian (LM Studio Backend).
Target OS: Arch Linux (Hyprland / Wayland) | Python 3.15+
Location: ~/user_scripts/llm/obsidian_ai_writing/setup_obsidian_ai_assistance.py
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def resolve_path(raw: str | Path) -> Path:
    """Expand environment variables ($HOME, $USER, etc.) and user home (~)."""
    return Path(os.path.expandvars(str(raw))).expanduser()


DEFAULT_CONFIG_PATH = resolve_path(
    "~/user_scripts/llm/obsidian_ai_writing/config.toml"
)

DEFAULT_SYSTEM_PROMPT = (
    "You are an elite intellectual writing engine. Every response must exhibit "
    "maximal rigor, semantic density, lexical sophistication, stylistic control, "
    "and analytical depth. Write exclusively in contemporary U.S. English at a "
    "PhD-plus register. Maximize information per word and state the decisive point first."
)


@dataclass(slots=True, frozen=True)
class HardwareReport:
    has_dedicated_gpu: bool
    gpu_vendor: str
    gpu_name: str
    vram_total_mib: int
    vram_free_mib: int
    vram_reclaimable_mib: int
    vram_effective_free_mib: int
    igpu_name: str
    system_ram_mib: int
    recommended_offload: str
    reason: str


@dataclass(slots=True, frozen=True)
class ModelSpec:
    catalog_key: str
    lms_id: str
    relative_gguf: str
    preferred_gpu_offload: str
    preferred_context_length: int
    min_free_vram_mib: int
    min_system_ram_mib: int


def log_info(msg: str) -> None:
    print(f"\033[1;34m[INFO]\033[0m {msg}")


def log_ok(msg: str) -> None:
    print(f"\033[1;32m[OK]\033[0m   {msg}")


def log_warn(msg: str) -> None:
    print(f"\033[1;33m[WARN]\033[0m {msg}", file=sys.stderr)


def log_err(msg: str) -> None:
    print(f"\033[1;31m[ERR]\033[0m  {msg}", file=sys.stderr)


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with config_path.open("rb") as f:
        return tomllib.load(f)


def atomic_write_bytes(
    target: Path, data: bytes, backup: bool = False
) -> bool:
    """
    Atomically write `data` to `target` via a sibling `.tmp` file and `os.replace()`.
    Returns False (without touching the filesystem) if `target` already has identical bytes.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        try:
            if target.read_bytes() == data:
                return False
        except OSError:
            pass
        if backup:
            bak_file = target.with_name(f"{target.name}.bak")
            try:
                shutil.copy2(target, bak_file)
            except OSError as exc:
                log_warn(f"Failed to create backup {bak_file}: {exc}")

    tmp_file = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with tmp_file.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, target)
        return True
    finally:
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass


def atomic_write_text(
    target: Path, content: str, backup: bool = False
) -> bool:
    """Atomically write UTF-8 text to `target` only if content differs."""
    return atomic_write_bytes(
        target, content.encode("utf-8"), backup=backup
    )


def get_system_ram_mib() -> int:
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return int(parts[1]) // 1024
    return 8192


def probe_pci_gpus() -> tuple[list[str], list[str]]:
    """Return (discrete_gpus, integrated_gpus) from lspci."""
    discrete: list[str] = []
    integrated: list[str] = []
    lspci = shutil.which("lspci")
    if not lspci:
        return discrete, integrated

    try:
        res = subprocess.run(
            [lspci], capture_output=True, text=True, timeout=5, check=False
        )
        for line in res.stdout.splitlines():
            if not any(
                k in line
                for k in (
                    "VGA compatible controller",
                    "3D controller",
                    "Display controller",
                )
            ):
                continue
            desc = line.split(":", 2)[-1].strip()
            lower = desc.lower()
            if "nvidia" in lower or any(
                d in lower for d in ("radeon rx", "navi", "vega 10", "vega 20")
            ):
                discrete.append(desc)
            else:
                integrated.append(desc)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log_warn(f"lspci probe failed: {exc}")

    return discrete, integrated


def normalize_gpu_offload(val: Any) -> str:
    """
    Normalize user-provided offload values from TOML or CLI into valid `lms load --gpu` arguments:
      - "cpu", "off", "none", "false", "no", "0", "0.0", 0 -> "off"
      - "gpu", "max", "full", "true", "all", "1", "1.0", 1, "100%" -> "max"
      - "auto" -> "auto"
      - "25%" -> "0.25"
      - 0.15 / "0.15" -> "0.15"
    """
    raw = str(val).strip().lower()
    if raw in ("off", "cpu", "none", "false", "no", "0", "0.0"):
        return "off"
    if raw in ("max", "gpu", "full", "true", "all", "1", "1.0", "100%"):
        return "max"
    if raw == "auto":
        return "auto"
    if raw.endswith("%"):
        try:
            pct = float(raw[:-1].strip()) / 100.0
            if 0.0 < pct < 1.0:
                return f"{pct:.2f}".rstrip("0").rstrip(".")
            return "max" if pct >= 1.0 else "off"
        except ValueError:
            pass
    try:
        num = float(raw)
        if num <= 0.0:
            return "off"
        if num >= 1.0:
            return "max"
        return f"{num:.2f}".rstrip("0").rstrip(".")
    except ValueError:
        log_warn(
            f"Unrecognized gpu offload value '{val}'; defaulting to 'off' for safety."
        )
        return "off"


def get_lms_vram_usage_mib() -> int:
    """
    Return the total NVIDIA VRAM (in MiB) currently allocated by LM Studio / llama.cpp
    compute processes (`lmstudio`, `lms`, `llama`, `bionic`).
    """
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return 0
    total_mib = 0
    try:
        res = subprocess.run(
            [
                nvidia_smi,
                "--query-compute-apps=process_name,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            for line in res.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if not parts:
                    continue
                proc_name = parts[0].lower()
                if any(k in proc_name for k in ("lmstudio", "lms", "llama", "bionic")):
                    if len(parts) > 1 and parts[1].isdigit():
                        total_mib += int(parts[1])
                    else:
                        total_mib = max(total_mib, 1)
    except Exception:
        pass
    return total_mib


def wait_for_vram_release(timeout_sec: float = 4.0) -> None:
    """
    Poll `get_lms_vram_usage_mib()` after `lms unload --all` until CUDA context teardown
    frees NVIDIA VRAM (or `timeout_sec` elapses).
    """
    if get_lms_vram_usage_mib() == 0:
        return
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if get_lms_vram_usage_mib() == 0:
            return


def get_state_file_path(cfg: dict[str, Any]) -> Path:
    paths_cfg = cfg.get("paths", {})
    raw = paths_cfg.get(
        "state_file",
        "~/user_scripts/llm/obsidian_ai_writing/.last_load_state.json",
    )
    return resolve_path(raw)


def read_load_state(state_file: Path) -> dict[str, Any]:
    if not state_file.is_file():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_load_state(
    state_file: Path,
    lms_id: str,
    gpu_offload: str,
    context_length: int,
    dry_run: bool = False,
) -> None:
    if dry_run:
        return
    payload = {
        "lms_id": lms_id,
        "gpu_offload": gpu_offload,
        "context_length": context_length,
        "updated_at": int(time.time()),
    }
    try:
        atomic_write_text(
            state_file, json.dumps(payload, indent=2) + "\n", backup=False
        )
    except OSError as exc:
        log_warn(f"Could not write load state file {state_file}: {exc}")


def detect_hardware(
    cfg: dict[str, Any],
    force_cpu: bool = False,
    override_offload: str | None = None,
) -> HardwareReport:
    hw_cfg = cfg.get("hardware", {})
    cfg_mode = str(hw_cfg.get("mode", "auto")).strip().lower()
    min_total_vram = int(hw_cfg.get("min_total_vram_mib", 3072))
    min_free_vram = int(hw_cfg.get("min_free_vram_mib", 2200))
    unload_first = bool(hw_cfg.get("unload_existing_before_load", True))

    norm_override = (
        normalize_gpu_offload(override_offload)
        if override_offload is not None
        else None
    )

    if force_cpu or norm_override == "off":
        effective_mode = "cpu"
    elif norm_override is not None:
        effective_mode = "gpu"
    else:
        effective_mode = cfg_mode

    gpu_offload = (
        norm_override
        if norm_override is not None
        else normalize_gpu_offload(hw_cfg.get("gpu_offload_when_dedicated", "max"))
    )
    partial_offload = normalize_gpu_offload(
        hw_cfg.get("gpu_offload_partial_retry", "0.5")
    )
    cpu_offload = normalize_gpu_offload(
        hw_cfg.get("gpu_offload_when_igpu_or_cpu", "off")
    )

    sys_ram_mib = get_system_ram_mib()
    discrete_pci, integrated_pci = probe_pci_gpus()
    igpu_name = integrated_pci[0] if integrated_pci else "None"

    if effective_mode == "cpu":
        return HardwareReport(
            has_dedicated_gpu=False,
            gpu_vendor="CPU",
            gpu_name="Forced CPU Mode",
            vram_total_mib=0,
            vram_free_mib=0,
            vram_reclaimable_mib=0,
            vram_effective_free_mib=0,
            igpu_name=igpu_name,
            system_ram_mib=sys_ram_mib,
            recommended_offload=cpu_offload,
            reason=(
                "CPU-only mode active via CLI (--cpu-only / --gpu-offload off) "
                "or hardware.mode='cpu' in config.toml."
            ),
        )

    # Probe NVIDIA GPU via nvidia-smi (total, free, and reclaimable LM Studio VRAM)
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            res = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                first_line = res.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in first_line.split(",")]
                gpu_name = parts[0] if parts else "NVIDIA GPU"
                vram_total = (
                    int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
                )
                vram_free = (
                    int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                )
                reclaimable = get_lms_vram_usage_mib() if unload_first else 0
                effective_free = min(vram_total, vram_free + reclaimable)

                if vram_total >= min_total_vram or effective_mode == "gpu":
                    offload = (
                        gpu_offload
                        if (
                            norm_override is not None
                            or effective_free >= min_free_vram
                        )
                        else partial_offload
                    )
                    reclaim_note = (
                        f" (+{reclaimable} MiB reclaimable from active LMS)"
                        if reclaimable > 0
                        else ""
                    )
                    reason = (
                        f"Active NVIDIA dGPU ({gpu_name}: {vram_free}/{vram_total} MiB free VRAM"
                        f"{reclaim_note}, effective free={effective_free} MiB"
                        f"{f', paired with {igpu_name}' if integrated_pci else ''})."
                    )
                    return HardwareReport(
                        has_dedicated_gpu=True,
                        gpu_vendor="NVIDIA",
                        gpu_name=gpu_name,
                        vram_total_mib=vram_total,
                        vram_free_mib=vram_free,
                        vram_reclaimable_mib=reclaimable,
                        vram_effective_free_mib=effective_free,
                        igpu_name=igpu_name,
                        system_ram_mib=sys_ram_mib,
                        recommended_offload=offload,
                        reason=reason,
                    )
                return HardwareReport(
                    has_dedicated_gpu=False,
                    gpu_vendor="NVIDIA",
                    gpu_name=gpu_name,
                    vram_total_mib=vram_total,
                    vram_free_mib=vram_free,
                    vram_reclaimable_mib=reclaimable,
                    vram_effective_free_mib=effective_free,
                    igpu_name=igpu_name,
                    system_ram_mib=sys_ram_mib,
                    recommended_offload=cpu_offload,
                    reason=(
                        f"NVIDIA GPU ({gpu_name}) total VRAM ({vram_total} MiB) is below "
                        f"minimum threshold ({min_total_vram} MiB)."
                    ),
                )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log_warn(f"nvidia-smi execution failed: {exc}")

    # Check if discrete AMD GPU is present
    for desc in discrete_pci:
        lower = desc.lower()
        if "amd" in lower or "ati" in lower:
            return HardwareReport(
                has_dedicated_gpu=True,
                gpu_vendor="AMD Discrete",
                gpu_name=desc,
                vram_total_mib=4096,
                vram_free_mib=3072,
                vram_reclaimable_mib=0,
                vram_effective_free_mib=3072,
                igpu_name=igpu_name,
                system_ram_mib=sys_ram_mib,
                recommended_offload=gpu_offload,
                reason=f"Detected discrete AMD GPU via lspci: {desc}",
            )
        if "nvidia" in lower:
            return HardwareReport(
                has_dedicated_gpu=False,
                gpu_vendor="NVIDIA (Dormant/Off)",
                gpu_name=desc,
                vram_total_mib=0,
                vram_free_mib=0,
                vram_reclaimable_mib=0,
                vram_effective_free_mib=0,
                igpu_name=igpu_name,
                system_ram_mib=sys_ram_mib,
                recommended_offload=cpu_offload,
                reason=(
                    f"NVIDIA dGPU ({desc}) detected on PCI bus, but driver/nvidia-smi "
                    "is inactive; falling back cleanly to CPU."
                ),
            )

    if integrated_pci:
        return HardwareReport(
            has_dedicated_gpu=False,
            gpu_vendor="Integrated GPU",
            gpu_name=igpu_name,
            vram_total_mib=0,
            vram_free_mib=0,
            vram_reclaimable_mib=0,
            vram_effective_free_mib=0,
            igpu_name=igpu_name,
            system_ram_mib=sys_ram_mib,
            recommended_offload=cpu_offload,
            reason=f"Integrated GPU only ({igpu_name}); using CPU inference (--gpu {cpu_offload}).",
        )

    return HardwareReport(
        has_dedicated_gpu=(effective_mode == "gpu"),
        gpu_vendor="CPU",
        gpu_name="Generic Host",
        vram_total_mib=0,
        vram_free_mib=0,
        vram_reclaimable_mib=0,
        vram_effective_free_mib=0,
        igpu_name="None",
        system_ram_mib=sys_ram_mib,
        recommended_offload=gpu_offload if effective_mode == "gpu" else cpu_offload,
        reason="No discrete GPU detected; defaulting to CPU mode.",
    )


def resolve_model_spec(
    cfg: dict[str, Any],
    hw: HardwareReport,
    cli_model: str | None = None,
    override_offload: str | None = None,
    override_context: int | None = None,
) -> ModelSpec:
    models_cfg = cfg.get("models", {})
    hw_cfg = cfg.get("hardware", {})
    catalog: dict[str, Any] = models_cfg.get("catalog", {})
    default_ctx = int(models_cfg.get("default_context_length", 8192))

    norm_override = (
        normalize_gpu_offload(override_offload)
        if override_offload is not None
        else None
    )

    # 1. Determine target model key: CLI flag > config.toml active_model > auto (GPU vs CPU default)
    chosen_key = cli_model
    if not chosen_key:
        cfg_active = str(models_cfg.get("active_model", "auto")).strip()
        if cfg_active and cfg_active.lower() != "auto":
            chosen_key = cfg_active
        else:
            chosen_key = str(
                models_cfg.get("default_gpu_model", "qwen3.5-4b")
                if hw.has_dedicated_gpu
                else models_cfg.get("default_cpu_model", "nanbeige4.2-3b")
            )

    # 2. Match against catalog keys or lms_id values
    matched_entry: dict[str, Any] | None = None
    matched_key = chosen_key
    if chosen_key in catalog and isinstance(catalog[chosen_key], dict):
        matched_entry = catalog[chosen_key]
    else:
        for k, v in catalog.items():
            if (
                isinstance(v, dict)
                and str(v.get("lms_id", "")).lower() == chosen_key.lower()
            ):
                matched_entry = v
                matched_key = k
                break

    if matched_entry:
        lms_id = str(matched_entry.get("lms_id", chosen_key))
        rel_gguf = str(matched_entry.get("relative_gguf", ""))
        raw_pref_offload = matched_entry.get(
            "preferred_gpu_offload",
            matched_entry.get("placement", hw.recommended_offload),
        )
        pref_offload = (
            norm_override
            if norm_override is not None
            else normalize_gpu_offload(raw_pref_offload)
        )
        pref_ctx = (
            override_context
            if override_context is not None
            else int(matched_entry.get("preferred_context_length", default_ctx))
        )
        min_vram = int(matched_entry.get("min_free_vram_mib", 2000))
        min_ram = int(matched_entry.get("min_system_ram_mib", 4096))
    else:
        lms_id = chosen_key
        rel_gguf = ""
        pref_offload = (
            norm_override if norm_override is not None else hw.recommended_offload
        )
        pref_ctx = override_context if override_context is not None else default_ctx
        min_vram = 2000
        min_ram = 4096

    effective_offload = pref_offload if hw.has_dedicated_gpu else "off"
    if norm_override is not None:
        effective_offload = (
            norm_override
            if (hw.has_dedicated_gpu or norm_override == "off")
            else "off"
        )

    # 3. Inspect GGUF size on disk against VRAM limits when CLI did not force a specific ratio
    models_dir = resolve_path(
        cfg.get("paths", {}).get("models_dir", "/mnt/media/downloads/llm/models")
    )
    kv_reserve_mib = int(hw_cfg.get("vram_kv_reserve_mib", 512))
    oversized_policy = str(hw_cfg.get("oversized_model_policy", "cpu")).strip().lower()
    partial_ratio = normalize_gpu_offload(
        hw_cfg.get("gpu_offload_partial_retry", "0.5")
    )

    if rel_gguf and (models_dir / rel_gguf).is_file():
        file_size_mib = (models_dir / rel_gguf).stat().st_size // (1024 * 1024)
        if (
            hw.has_dedicated_gpu
            and effective_offload == "max"
            and norm_override is None
        ):
            if file_size_mib + kv_reserve_mib > hw.vram_total_mib:
                replacement = partial_ratio if oversized_policy == "partial" else "off"
                log_warn(
                    f"Model '{lms_id}' size ({file_size_mib} MiB + {kv_reserve_mib} MiB KV reserve) "
                    f"exceeds total VRAM ({hw.vram_total_mib} MiB). "
                    f"Switching offload from 'max' to '{replacement}' (oversized_model_policy='{oversized_policy}')."
                )
                effective_offload = replacement

    # Compare against `vram_effective_free_mib` (which includes reclaimable LMS VRAM)
    if (
        hw.has_dedicated_gpu
        and effective_offload == "max"
        and norm_override is None
        and hw.vram_effective_free_mib > 0
        and hw.vram_effective_free_mib < min_vram
    ):
        log_warn(
            f"Effective free VRAM ({hw.vram_effective_free_mib} MiB) is below model '{lms_id}' "
            f"threshold ({min_vram} MiB); adjusting initial offload to --gpu {partial_ratio}."
        )
        effective_offload = partial_ratio

    if hw.system_ram_mib < min_ram:
        log_warn(
            f"System RAM ({hw.system_ram_mib} MiB) is below recommended {min_ram} MiB "
            f"for model '{lms_id}'."
        )

    return ModelSpec(
        catalog_key=matched_key,
        lms_id=lms_id,
        relative_gguf=rel_gguf,
        preferred_gpu_offload=effective_offload,
        preferred_context_length=pref_ctx,
        min_free_vram_mib=min_vram,
        min_system_ram_mib=min_ram,
    )


def verify_local_gguf_exists(cfg: dict[str, Any], spec: ModelSpec) -> Path | None:
    if not spec.relative_gguf:
        return None
    models_dir = resolve_path(
        cfg.get("paths", {}).get("models_dir", "/mnt/media/downloads/llm/models")
    )
    gguf_path = models_dir / spec.relative_gguf
    if gguf_path.is_file():
        size_gib = gguf_path.stat().st_size / (1024**3)
        log_ok(f"Verified GGUF file on disk: {gguf_path} ({size_gib:.2f} GiB)")
        return gguf_path
    log_warn(f"Expected GGUF file not found at {gguf_path}")
    return None


def is_server_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    url = f"http://{host}:{port}/v1/models"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def get_active_server_models(host: str, port: int) -> list[str]:
    url = f"http://{host}:{port}/v1/models"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            data = payload.get("data", [])
            return [str(item["id"]) for item in data if "id" in item]
    except Exception:
        return []


def resolve_lms_load_target(lms_path: Path, spec: ModelSpec) -> str:
    """
    Match `spec.lms_id` or `spec.relative_gguf` against `lms ls --json` (with plain-text fallback),
    so `lms load` succeeds whether LM Studio indexes by folder path or short key.
    """
    stem = Path(spec.relative_gguf).stem.lower() if spec.relative_gguf else ""
    try:
        json_res = subprocess.run(
            [str(lms_path), "ls", "--json"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if json_res.returncode == 0 and json_res.stdout.strip().startswith("["):
            items = json.loads(json_res.stdout)
            for item in items:
                if not isinstance(item, dict):
                    continue
                for field in (
                    "modelKey",
                    "indexedModelIdentifier",
                    "path",
                    "identifier",
                ):
                    val = str(item.get(field, "")).strip()
                    if not val:
                        continue
                    if val.lower() == spec.lms_id.lower():
                        return val
                    if stem and stem in val.lower():
                        return val
    except Exception:
        pass

    try:
        res = subprocess.run(
            [str(lms_path), "ls"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.splitlines():
                tokens = line.strip().split()
                if not tokens:
                    continue
                candidate = tokens[0]
                if spec.lms_id.lower() == candidate.lower():
                    return candidate
                if stem and (
                    stem in candidate.lower()
                    or spec.lms_id.lower() in candidate.lower()
                ):
                    return candidate
    except Exception:
        pass
    return spec.lms_id


def needs_placement_reload(
    spec: ModelSpec,
    last_state: dict[str, Any],
    lms_vram_mib: int,
    has_nvidia_smi: bool,
) -> tuple[bool, str]:
    """
    Determine whether an already-loaded model must be unloaded and reloaded because
    its requested placement (`--gpu` or `-c`) changed in `config.toml` or via CLI.
    """
    if spec.preferred_gpu_offload == "off" and lms_vram_mib > 0:
        return (
            True,
            f"Target placement is CPU ('--gpu off'), but LM Studio currently holds {lms_vram_mib} MiB of NVIDIA VRAM.",
        )

    if (
        has_nvidia_smi
        and spec.preferred_gpu_offload != "off"
        and lms_vram_mib == 0
    ):
        return (
            True,
            f"Target placement requests GPU offload ('--gpu {spec.preferred_gpu_offload}'), "
            "but model is currently resident in CPU RAM only (0 MiB NVIDIA VRAM used).",
        )

    if last_state:
        prev_id = str(last_state.get("lms_id", "")).lower()
        prev_gpu = str(last_state.get("gpu_offload", ""))
        prev_ctx = int(last_state.get("context_length", 0))
        if prev_id == spec.lms_id.lower():
            if prev_gpu and prev_gpu != spec.preferred_gpu_offload:
                return (
                    True,
                    f"Configured GPU offload changed from '--gpu {prev_gpu}' to '--gpu {spec.preferred_gpu_offload}'.",
                )
            if prev_ctx and prev_ctx != spec.preferred_context_length:
                return (
                    True,
                    f"Configured context length changed from {prev_ctx} to {spec.preferred_context_length}.",
                )

    return False, ""


def run_lms_load_with_fallback(
    lms_path: Path,
    cfg: dict[str, Any],
    hw: HardwareReport,
    spec: ModelSpec,
    force_reload: bool = False,
) -> str:
    """
    3-Stage Progressive Load Strategy:
      1. Load target model with `spec.preferred_gpu_offload` and `spec.preferred_context_length`.
      2. If Stage 1 fails and GPU offload was enabled, retry target model with partial offload (`--gpu 0.5`) and fallback context (`4096`).
      3. If prior stages fail and `fallback_to_cpu_on_error` is true, unload and load `default_cpu_model` with `--gpu off`.
    """
    hw_cfg = cfg.get("hardware", {})
    models_cfg = cfg.get("models", {})
    unload_first = bool(hw_cfg.get("unload_existing_before_load", True))
    cfg_force_reload = bool(hw_cfg.get("force_reload_on_run", False))
    fallback_on_error = bool(hw_cfg.get("fallback_to_cpu_on_error", True))
    partial_ratio = normalize_gpu_offload(
        hw_cfg.get("gpu_offload_partial_retry", "0.5")
    )
    fallback_ctx = int(models_cfg.get("fallback_context_length", 4096))
    load_timeout = int(hw_cfg.get("load_timeout_sec", 180))
    vram_release_timeout = float(hw_cfg.get("vram_release_timeout_sec", 4.0))

    state_file = get_state_file_path(cfg)
    last_state = read_load_state(state_file)
    lms_vram_mib = get_lms_vram_usage_mib()
    has_nvidia_smi = shutil.which("nvidia-smi") is not None

    should_reload = force_reload or cfg_force_reload
    placement_changed, change_reason = needs_placement_reload(
        spec, last_state, lms_vram_mib, has_nvidia_smi
    )
    if placement_changed:
        log_info(f"Placement change detected: {change_reason}")
        should_reload = True

    ps_res = subprocess.run(
        [str(lms_path), "ps"], capture_output=True, text=True, check=False
    )
    if ps_res.returncode == 0 and ps_res.stdout.strip():
        already_loaded = spec.lms_id.lower() in ps_res.stdout.lower()
        if already_loaded and not should_reload:
            log_ok(
                f"Model '{spec.lms_id}' is already loaded with matching placement "
                f"(--gpu {spec.preferred_gpu_offload}, -c {spec.preferred_context_length})."
            )
            write_load_state(
                state_file,
                spec.lms_id,
                spec.preferred_gpu_offload,
                spec.preferred_context_length,
            )
            return spec.lms_id
        if unload_first or should_reload:
            log_info(
                "Unloading resident model(s) and waiting for VRAM/RAM release..."
            )
            subprocess.run(
                [str(lms_path), "unload", "--all"],
                capture_output=True,
                text=True,
                check=False,
            )
            wait_for_vram_release(timeout_sec=vram_release_timeout)

    load_key = resolve_lms_load_target(lms_path, spec)

    # 5-tuple: (stage_label, lms_load_arg, logical_lms_id, gpu_arg, ctx_arg)
    attempts: list[tuple[str, str, str, str, int]] = [
        (
            "Stage 1 (Primary)",
            load_key,
            spec.lms_id,
            spec.preferred_gpu_offload,
            spec.preferred_context_length,
        )
    ]

    # Stage 2: Partial GPU retry (only when Stage 1 used GPU offload != partial_ratio)
    if (
        spec.preferred_gpu_offload != "off"
        and spec.preferred_gpu_offload != partial_ratio
    ):
        attempts.append(
            (
                "Stage 2 (Partial GPU Retry)",
                load_key,
                spec.lms_id,
                partial_ratio,
                fallback_ctx,
            )
        )

    # Stage 3: Safe CPU fallback (when GPU load fails OR when a heavy CPU model fails)
    if fallback_on_error:
        cpu_model_key = str(models_cfg.get("default_cpu_model", spec.lms_id))
        cpu_spec = resolve_model_spec(
            cfg, hw, cli_model=cpu_model_key, override_offload="off"
        )
        cpu_load_key = resolve_lms_load_target(lms_path, cpu_spec)
        if (cpu_load_key, "off") != (load_key, spec.preferred_gpu_offload):
            attempts.append(
                (
                    "Stage 3 (CPU Fallback)",
                    cpu_load_key,
                    cpu_spec.lms_id,
                    "off",
                    fallback_ctx,
                )
            )

    for stage_label, model_arg, logical_id, gpu_arg, ctx_arg in attempts:
        cmd = [
            str(lms_path),
            "load",
            model_arg,
            "--gpu",
            gpu_arg,
            "-c",
            str(ctx_arg),
            "-y",
        ]
        log_info(f"[{stage_label}] Executing: {' '.join(cmd)}")
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=load_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log_warn(f"[{stage_label}] Timed out after {load_timeout}s.")
            subprocess.run(
                [str(lms_path), "unload", "--all"],
                capture_output=True,
                text=True,
                check=False,
            )
            wait_for_vram_release(timeout_sec=vram_release_timeout)
            continue

        if res.returncode == 0:
            log_ok(
                f"[{stage_label}] Loaded '{logical_id}' (target='{model_arg}', --gpu {gpu_arg}, -c {ctx_arg})."
            )
            write_load_state(state_file, logical_id, gpu_arg, ctx_arg)
            return logical_id

        err_detail = (res.stderr or res.stdout).strip()
        log_warn(f"[{stage_label}] Failed (exit {res.returncode}): {err_detail}")
        subprocess.run(
            [str(lms_path), "unload", "--all"],
            capture_output=True,
            text=True,
            check=False,
        )
        wait_for_vram_release(timeout_sec=vram_release_timeout)

    log_warn(
        f"All automatic `lms load` stages exited non-zero; keeping '{spec.lms_id}' in plugin config."
    )
    return spec.lms_id


def ensure_lmstudio_server_and_model(
    cfg: dict[str, Any],
    hw: HardwareReport,
    spec: ModelSpec,
    dry_run: bool = False,
    force_reload: bool = False,
) -> str:
    paths_cfg = cfg.get("paths", {})
    srv_cfg = cfg.get("server", {})

    lms_path = resolve_path(paths_cfg.get("lms_cli", "~/.lmstudio/bin/lms"))
    appimage_path = resolve_path(
        paths_cfg.get(
            "lmstudio_appimage",
            "/mnt/media/downloads/llm/lmstudio/Bionic-1.2.0-2-linux-x64.AppImage",
        )
    )

    host = str(srv_cfg.get("host", "127.0.0.1"))
    port = int(srv_cfg.get("port", 1234))
    cors = bool(srv_cfg.get("cors", True))
    timeout_sec = int(srv_cfg.get("startup_timeout_sec", 30))

    verify_local_gguf_exists(cfg, spec)

    if not lms_path.is_file():
        which_lms = shutil.which("lms")
        if which_lms:
            lms_path = Path(which_lms)
        else:
            log_warn(
                f"LM Studio CLI not found at {lms_path}. Skipping live server startup; "
                f"configuring Obsidian plugins for model '{spec.lms_id}'."
            )
            return spec.lms_id

    if is_server_reachable(host, port):
        log_ok(f"LM Studio server already active at http://{host}:{port}/v1")
    else:
        start_cmd = [str(lms_path), "server", "start", "-p", str(port)]
        if cors:
            start_cmd.append("--cors")
        log_info(f"Starting LM Studio HTTP server: {' '.join(start_cmd)}")

        if not dry_run:
            res = subprocess.run(
                start_cmd, capture_output=True, text=True, check=False
            )
            if res.returncode != 0 and not is_server_reachable(host, port):
                subprocess.run(
                    [str(lms_path), "daemon", "up"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                subprocess.run(
                    start_cmd, capture_output=True, text=True, check=False
                )

            if not is_server_reachable(host, port) and appimage_path.is_file():
                log_warn(
                    "LMS daemon requires GUI bootstrap; launching LM Studio AppImage..."
                )
                env = os.environ.copy()
                subprocess.Popen(
                    [str(appimage_path), "--no-sandbox"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    start_new_session=True,
                )

            deadline = time.monotonic() + timeout_sec
            while time.monotonic() < deadline:
                if is_server_reachable(host, port):
                    log_ok(f"LM Studio server listening at http://{host}:{port}/v1")
                    break
                time.sleep(1.0)
                subprocess.run(
                    start_cmd, capture_output=True, text=True, check=False
                )
            else:
                log_warn(
                    f"LM Studio server did not respond within {timeout_sec}s. "
                    "Proceeding with static Obsidian configuration."
                )

    if dry_run:
        log_info(
            f"[DRY-RUN] Would load '{spec.lms_id}' with --gpu {spec.preferred_gpu_offload} "
            f"-c {spec.preferred_context_length}"
        )
        return spec.lms_id

    loaded_id = run_lms_load_with_fallback(
        lms_path, cfg, hw, spec, force_reload=force_reload
    )

    # Query /v1/models to verify exact API identifier ("This model's API identifier")
    live_models = get_active_server_models(host, port)
    if live_models:
        for m_id in live_models:
            if loaded_id.lower() == m_id.lower():
                log_ok(f"Verified exact API identifier from /v1/models: '{m_id}'")
                return m_id
        for m_id in live_models:
            if loaded_id.lower() in m_id.lower() or m_id.lower() in loaded_id.lower():
                log_ok(f"Resolved active API identifier from /v1/models: '{m_id}'")
                return m_id

    return loaded_id


def read_system_prompt(cfg: dict[str, Any]) -> str:
    inst_path = resolve_path(
        cfg.get("paths", {}).get(
            "instructions_file", "/mnt/media/downloads/llm/instructions.md"
        )
    )
    if inst_path.is_file():
        content = inst_path.read_text(encoding="utf-8").strip()
        if content:
            log_ok(f"Loaded system prompt ({len(content)} chars) from {inst_path}")
            return content
    log_warn(
        f"Instructions file not found at {inst_path}; using built-in PhD U.S.-English fallback."
    )
    return DEFAULT_SYSTEM_PROMPT


def has_valid_plugin_binaries(directory: Path) -> bool:
    main_js = directory / "main.js"
    manifest_json = directory / "manifest.json"
    return (
        main_js.is_file()
        and main_js.stat().st_size > 0
        and manifest_json.is_file()
        and manifest_json.stat().st_size > 0
    )


def sync_plugin_assets(src_dir: Path, dst_dir: Path, assets: list[str]) -> int:
    """Copy `assets` from `src_dir` to `dst_dir` atomically only when missing or changed."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    updated_count = 0
    for asset_name in assets:
        src_file = src_dir / asset_name
        if not src_file.is_file():
            continue
        if atomic_write_bytes(
            dst_dir / asset_name, src_file.read_bytes(), backup=False
        ):
            updated_count += 1
    return updated_count


def install_plugin_with_cache(
    github_repo: str,
    plugin_id: str,
    plugin_dir: Path,
    cache_root: Path,
    assets: list[str],
    prefer_local_cache: bool = True,
    force_update: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Cache-first plugin installer:
      1. Unless `force_update` (`--update-plugins`) is True, if `prefer_local_cache` is enabled and
         `cache_root/<plugin_id>/` has valid binaries, sync directly from cache with zero network traffic.
      2. If cache is empty but `plugin_dir` already has valid binaries, back-populate cache and return.
      3. Otherwise (or when `--update-plugins` is passed), fetch latest release from GitHub API and update both.
    """
    cache_dir = cache_root / plugin_id

    if dry_run:
        mode_desc = (
            "GitHub release update"
            if force_update
            else "local cache (or GitHub if uncached)"
        )
        log_info(
            f"[DRY-RUN] Would ensure plugin '{plugin_id}' via {mode_desc} -> {plugin_dir}"
        )
        return True

    if prefer_local_cache and not force_update:
        if has_valid_plugin_binaries(cache_dir):
            changed = sync_plugin_assets(cache_dir, plugin_dir, assets)
            status_note = (
                f"synced {changed} updated file(s)"
                if changed
                else "binaries already up-to-date"
            )
            log_ok(
                f"Verified plugin '{plugin_id}' from local cache ({status_note}; pass --update-plugins to fetch latest)."
            )
            return True

        if has_valid_plugin_binaries(plugin_dir):
            sync_plugin_assets(plugin_dir, cache_dir, assets)
            log_ok(
                f"Verified existing '{plugin_id}' in vault and seeded local cache ({cache_dir})."
            )
            return True

    # Online GitHub release fetch (when cache is missing or --update-plugins is passed)
    api_url = f"https://api.github.com/repos/{github_repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Obsidian-LMStudio-Setup-ArchLinux/3.0",
    }
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            release_data = json.loads(resp.read().decode("utf-8"))

        asset_map = {
            str(item["name"]): str(item["browser_download_url"])
            for item in release_data.get("assets", [])
            if "name" in item and "browser_download_url" in item
        }
        tag_name = str(release_data.get("tag_name", "latest"))
        log_info(f"Fetching {github_repo} ({tag_name}) -> {plugin_dir}")

        for asset_name in assets:
            dl_url = asset_map.get(asset_name)
            if not dl_url:
                if asset_name == "styles.css":
                    continue
                log_warn(
                    f"Release {tag_name} of {github_repo} does not contain {asset_name}"
                )
                continue

            asset_req = urllib.request.Request(dl_url, headers=headers)
            with urllib.request.urlopen(asset_req, timeout=30) as asset_resp:
                raw_bytes = asset_resp.read()

            atomic_write_bytes(plugin_dir / asset_name, raw_bytes, backup=False)
            atomic_write_bytes(cache_dir / asset_name, raw_bytes, backup=False)
            log_ok(f"  Downloaded & cached {asset_name} ({len(raw_bytes)} bytes)")

        if has_valid_plugin_binaries(plugin_dir):
            return True
    except Exception as exc:
        log_warn(
            f"Online GitHub fetch for {github_repo} failed ({exc}); falling back to local cache..."
        )

    if has_valid_plugin_binaries(cache_dir):
        sync_plugin_assets(cache_dir, plugin_dir, assets)
        log_ok(f"Installed '{plugin_id}' from local offline cache ({cache_dir}).")
        return True

    if has_valid_plugin_binaries(plugin_dir):
        log_ok(f"Using existing plugin binaries in {plugin_dir}.")
        return True

    log_err(
        f"Could not install '{plugin_id}': offline and no cached binaries in {cache_dir} or {plugin_dir}."
    )
    return False


def deep_merge_dict(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for k, v in overrides.items():
        if k in merged and isinstance(merged[k], dict) and isinstance(v, dict):
            merged[k] = deep_merge_dict(merged[k], v)
        else:
            merged[k] = copy.deepcopy(v)
    return merged


def build_text_generator_data_json(
    cfg: dict[str, Any], active_model: str, system_prompt: str
) -> dict[str, Any]:
    srv = cfg.get("server", {})
    gen = cfg.get("generation", {})
    plg = cfg.get("plugins", {})

    host = str(srv.get("host", "127.0.0.1"))
    port = int(srv.get("port", 1234))
    chat_path = str(srv.get("chat_completions_path", "/v1/chat/completions"))
    endpoint = f"http://{host}:{port}{chat_path}"
    api_key = str(srv.get("api_key_placeholder", f"http://{host}:{port}/"))
    profile_name = str(srv.get("provider_profile_name", "fastgpu"))
    cors_bypass = bool(srv.get("cors_bypass_in_plugin", False))

    max_tokens = int(gen.get("max_tokens", 2048))
    temperature = float(gen.get("temperature", 0.35))
    freq_penalty = float(gen.get("frequency_penalty", 0.1))
    pres_penalty = float(gen.get("presence_penalty", 0.0))
    streaming = bool(gen.get("streaming", True))
    prompts_dir = str(plg.get("prompts_relative_dir", "textgenerator/prompts"))

    provider_block: dict[str, Any] = {
        "endpoint": endpoint,
        "api_key": api_key,
        "model": active_model,
        "streaming": streaming,
        "CORSBypass": cors_bypass,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "frequency_penalty": freq_penalty,
        "presence_penalty": pres_penalty,
        "custom_default_system_prompt": system_prompt,
        "custom_header": (
            "{\n"
            '  "Content-Type": "application/json",\n'
            '  "Authorization": "Bearer {{api_key}}"\n'
            "}"
        ),
        "handlebars_body_in": (
            "{\n"
            '  "model": "{{model}}",\n'
            '  "temperature": {{temperature}},\n'
            '  "max_tokens": {{max_tokens}},\n'
            '  "frequency_penalty": {{frequency_penalty}},\n'
            '  "presence_penalty": {{presence_penalty}},\n'
            '  "stream": {{#if stream}}{{stream}}{{else}}false{{/if}},\n'
            '  "messages": {{{ stringify messages }}}\n'
            "}"
        ),
        "path_to_choices": "choices.0.message.content",
        "path_to_message_content": "choices.0.delta.content",
    }

    return {
        "endpoint": endpoint,
        "api_key": api_key,
        "model": active_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "frequency_penalty": freq_penalty,
        "promptsPath": prompts_dir,
        "textGenPath": "textgenerator/",
        "showStatusBar": True,
        "displayErrorInEditor": False,
        "outputToBlockQuote": False,
        "freeCursorOnStreaming": True,
        "experiment": False,
        "includeAttachmentsInRequest": True,
        "selectedProvider": "Default (Custom)",
        "LLMProviderProfiles": {
            profile_name: {
                "extends": "Default (Custom)",
                "name": profile_name,
            }
        },
        "LLMProviderOptions": {
            "Default (Custom)": provider_block,
            profile_name: copy.deepcopy(provider_block),
        },
        "options": {
            "generate-text": True,
            "generate-text-with-metadata": True,
            "insert-generated-text-From-template": True,
            "create-generated-text-From-template": True,
            "search-results-batch-generate-from-template": True,
            "insert-text-From-template": True,
            "create-text-From-template": True,
            "show-modal-From-template": True,
            "set_max_tokens": True,
            "set-llm": True,
            "packageManager": True,
            "create-template": True,
            "get-title": True,
            "generated-text-to-clipboard-From-template": True,
            "calculate-tokens": True,
            "calculate-tokens-for-template": True,
            "text-extractor-tool": True,
            "stop-stream": True,
            "custom-instruct": True,
            "generate-in-right-click-menu": True,
            "batch-generate-in-right-click-files-menu": True,
            "tg-block-processor": True,
            "reload": True,
        },
        "context": {
            "includeTitle": False,
            "includeStaredBlocks": True,
            "includeFrontmatter": False,
            "includeHeadings": False,
            "includeChildren": False,
            "includeMentions": False,
            "includeHighlights": True,
            "includeExtractions": False,
            "includeClipboard": False,
            "customInstructEnabled": True,
            "customInstruct": "{{systemPrompt}}\n\nTask: {{tg_selection}}",
            "contextTemplate": "{{tg_selection}}",
        },
    }


def merge_smart_composer_data_json(
    existing: dict[str, Any],
    cfg: dict[str, Any],
    active_model: str,
    system_prompt: str,
) -> dict[str, Any]:
    srv = cfg.get("server", {})
    host = str(srv.get("host", "127.0.0.1"))
    port = int(srv.get("port", 1234))
    base_v1 = f"http://{host}:{port}{srv.get('base_v1_path', '/v1')}"

    merged = copy.deepcopy(existing)
    providers: list[dict[str, Any]] = list(merged.get("providers", []))
    lm_provider = {
        "id": "lm-studio",
        "type": "lm-studio",
        "baseUrl": base_v1,
        "apiKey": "lm-studio",
    }
    replaced_prov = False
    for idx, p in enumerate(providers):
        if isinstance(p, dict) and p.get("id") == "lm-studio":
            providers[idx] = deep_merge_dict(p, lm_provider)
            replaced_prov = True
            break
    if not replaced_prov:
        providers.append(lm_provider)
    merged["providers"] = providers

    # Collect all catalog model IDs plus active_model so the user can switch in UI at any time
    catalog: dict[str, Any] = cfg.get("models", {}).get("catalog", {})
    all_lms_ids: list[str] = [active_model]
    for entry in catalog.values():
        if isinstance(entry, dict) and entry.get("lms_id"):
            cid = str(entry["lms_id"])
            if cid not in all_lms_ids:
                all_lms_ids.append(cid)

    chat_models: list[dict[str, Any]] = list(merged.get("chatModels", []))
    for mid in all_lms_ids:
        entry_id = f"lmstudio-{mid}"
        model_entry = {
            "id": entry_id,
            "providerId": "lm-studio",
            "providerType": "lm-studio",
            "model": mid,
            "enable": True,
        }
        replaced_model = False
        for idx, m in enumerate(chat_models):
            if isinstance(m, dict) and m.get("id") == entry_id:
                chat_models[idx] = deep_merge_dict(m, model_entry)
                replaced_model = True
                break
        if not replaced_model:
            chat_models.append(model_entry)
    merged["chatModels"] = chat_models

    active_entry_id = f"lmstudio-{active_model}"
    merged["chatModelId"] = active_entry_id
    merged["applyModelId"] = active_entry_id
    merged["systemPrompt"] = system_prompt
    merged.setdefault(
        "chatOptions",
        {
            "includeCurrentFileContent": True,
            "enableTools": False,
            "maxAutoIterations": 1,
        },
    )
    return merged


def build_copilot_data_json(
    cfg: dict[str, Any], active_model: str, system_prompt: str
) -> dict[str, Any]:
    srv = cfg.get("server", {})
    host = str(srv.get("host", "127.0.0.1"))
    port = int(srv.get("port", 1234))
    base_v1 = f"http://{host}:{port}{srv.get('base_v1_path', '/v1')}"

    return {
        "defaultChainType": "llm_chain",
        "defaultModelKey": f"{active_model}|lm-studio",
        "userSystemPrompt": system_prompt,
        "stream": True,
        "enableEncryption": False,
        "customModels": [
            {
                "name": active_model,
                "provider": "lm-studio",
                "enabled": True,
                "isBuiltIn": False,
                "endpointAddress": base_v1,
                "apiKey": "lm-studio",
                "capabilities": ["chat"],
            }
        ],
    }


def write_plugin_data_json(
    obsidian_config_dir: Path,
    plugin_id: str,
    new_payload: dict[str, Any],
    backup: bool = True,
    custom_merger: Any | None = None,
    dry_run: bool = False,
) -> None:
    plugin_dir = obsidian_config_dir / "plugins" / plugin_id
    data_file = plugin_dir / "data.json"
    existing_data: dict[str, Any] = {}

    if data_file.is_file():
        try:
            loaded = json.loads(data_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_data = loaded
        except Exception as exc:
            log_warn(
                f"Existing {data_file} had invalid JSON ({exc}); replacing cleanly."
            )

    if custom_merger is not None:
        final_payload = custom_merger(existing_data)
    else:
        final_payload = deep_merge_dict(existing_data, new_payload)

    serialized = json.dumps(final_payload, indent=2, ensure_ascii=False) + "\n"

    if dry_run:
        log_info(
            f"[DRY-RUN] Would atomically write {data_file} ({len(serialized)} bytes)"
        )
        return

    changed = atomic_write_text(data_file, serialized, backup=backup)
    if changed:
        log_ok(f"Updated configuration: {data_file}")
    else:
        log_ok(f"Configuration already up-to-date: {data_file}")


def enable_community_plugins(
    obsidian_config_dir: Path, plugin_ids: list[str], dry_run: bool = False
) -> None:
    cp_file = obsidian_config_dir / "community-plugins.json"
    existing: list[str] = []

    if cp_file.is_file():
        try:
            loaded = json.loads(cp_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = [str(x) for x in loaded]
        except Exception as exc:
            log_warn(f"Could not parse existing {cp_file}: {exc}")

    for pid in plugin_ids:
        if pid not in existing:
            existing.append(pid)

    serialized = json.dumps(existing, indent=2) + "\n"
    if dry_run:
        log_info(f"[DRY-RUN] Would update {cp_file} with: {existing}")
        return

    atomic_write_text(cp_file, serialized, backup=False)
    log_ok(f"Enabled community plugins in {cp_file}: {', '.join(existing)}")


def seed_writing_prompt_templates(
    vault_dir: Path, prompts_rel_dir: str, dry_run: bool = False
) -> None:
    target_dir = vault_dir / prompts_rel_dir / "intellectual-writing"
    author_tag = os.environ.get("USER") or Path.home().name or "author"
    templates: dict[str, str] = {
        "compress-and-sharpen.md": (
            "---\n"
            "PromptInfo:\n"
            "  promptId: compress-and-sharpen\n"
            "  name: Compress & Sharpen (PhD U.S. Register)\n"
            "  description: Relentlessly compress selection while maximizing semantic density and U.S. idiomatic force.\n"
            "  required_values: []\n"
            f"  author: {author_tag}\n"
            "  tags:\n"
            "    - writing\n"
            "    - editing\n"
            "  version: 1.0.0\n"
            "---\n"
            "Rewrite the following passage to maximize semantic density, lexical precision, and rhetorical force in contemporary U.S. English. "
            "Eliminate all throat-clearing, redundancy, and filler while preserving every substantive qualification:\n\n"
            "{{tg_selection}}\n"
        ),
        "expand-analytically.md": (
            "---\n"
            "PromptInfo:\n"
            "  promptId: expand-analytically\n"
            "  name: Expand Analytically (Mechanisms & Second-Order Effects)\n"
            "  description: Unpack causal mechanisms, hidden premises, and second-order implications.\n"
            "  required_values: []\n"
            f"  author: {author_tag}\n"
            "  tags:\n"
            "    - writing\n"
            "    - analysis\n"
            "  version: 1.0.0\n"
            "---\n"
            "Develop and rigorously extend the argument below. Articulate the underlying causal mechanisms, unstated premises, "
            "necessary vs. sufficient conditions, and second-order consequences without padding:\n\n"
            "{{tg_selection}}\n"
        ),
        "steelman-and-counterargue.md": (
            "---\n"
            "PromptInfo:\n"
            "  promptId: steelman-and-counterargue\n"
            "  name: Steelman & Incisive Counterargument\n"
            "  description: Formulate the strongest possible counter-thesis and expose structural vulnerabilities.\n"
            "  required_values: []\n"
            f"  author: {author_tag}\n"
            "  tags:\n"
            "    - critique\n"
            "    - reasoning\n"
            "  version: 1.0.0\n"
            "---\n"
            "Subject the following claim or passage to rigorous adversarial scrutiny. State the strongest steelmanned counterargument first, "
            "identify any conflation of correlation with causation or description with explanation, and specify the exact evidentiary conditions required to adjudicate the dispute:\n\n"
            "{{tg_selection}}\n"
        ),
        "us-idiomatic-polish.md": (
            "---\n"
            "PromptInfo:\n"
            "  promptId: us-idiomatic-polish\n"
            "  name: U.S. Idiomatic & Lexical Elevation\n"
            "  description: Replace flat or Commonwealth phrasing with high-resolution U.S. English diction and idiom.\n"
            "  required_values: []\n"
            f"  author: {author_tag}\n"
            "  tags:\n"
            "    - style\n"
            "    - polish\n"
            "  version: 1.0.0\n"
            "---\n"
            "Elevate the prose below into formidable, natural U.S. English. Replace any generic verbs, Commonwealth turns of phrase, "
            "or slack syntax with high-resolution domain terminology and native American idiomatic cadence:\n\n"
            "{{tg_selection}}\n"
        ),
    }

    if dry_run:
        log_info(
            f"[DRY-RUN] Would seed {len(templates)} writing templates in {target_dir}"
        )
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in templates.items():
        atomic_write_text(target_dir / filename, content, backup=False)
    log_ok(f"Verified {len(templates)} writing templates in {target_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automated Obsidian + LM Studio AI Writing Setup (Arch Linux / Python 3.15)"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to TOML configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "-m",
        "--model",
        type=str,
        default=None,
        help="Override target model key (e.g. qwen3.5-4b, nanbeige4.2-3b, lfm3-1.7b, huihui-qwen3.6-35b-a3b)",
    )
    parser.add_argument(
        "--cpu-only",
        action="store_true",
        help="Force CPU inference (--gpu off) regardless of detected GPU hardware",
    )
    parser.add_argument(
        "--gpu-offload",
        type=str,
        default=None,
        help="Override GPU offload ratio: 'off'/'cpu', 'max'/'gpu', or float '0.0'-'1.0' (e.g. '0.5' or '50%%')",
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=None,
        help="Override context length passed to `lms load`",
    )
    parser.add_argument(
        "--force-reload",
        action="store_true",
        help="Force unloading and reloading the target model even if already present in `lms ps`",
    )
    parser.add_argument(
        "--update-plugins",
        action="store_true",
        help="Force re-fetching the latest plugin releases from GitHub into the local cache and vault",
    )
    parser.add_argument(
        "--skip-server",
        action="store_true",
        help="Configure Obsidian plugins and templates without starting LM Studio or loading models",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Report detected hardware, VRAM/RAM, LM Studio server status, and installed plugins, then exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview all actions without modifying files or starting processes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cfg = load_config(args.config)
    except Exception as exc:
        log_err(str(exc))
        return 1

    paths_cfg = cfg.get("paths", {})
    srv_cfg = cfg.get("server", {})
    plg_cfg = cfg.get("plugins", {})
    registry = plg_cfg.get("registry", {})

    vault_dir = resolve_path(
        paths_cfg.get("vault_dir", "~/Documents/pensive")
    )
    obsidian_config_dir = resolve_path(
        paths_cfg.get("obsidian_config_dir", str(vault_dir / ".obsidian"))
    )
    cache_root = resolve_path(
        paths_cfg.get(
            "plugin_cache_dir", "/mnt/media/downloads/llm/obsidian_plugin_cache"
        )
    )

    hw = detect_hardware(
        cfg, force_cpu=args.cpu_only, override_offload=args.gpu_offload
    )
    log_info(
        f"Hardware Probe: dGPU={hw.gpu_vendor} ({hw.gpu_name}) | "
        f"VRAM={hw.vram_free_mib}/{hw.vram_total_mib} MiB free "
        f"(effective={hw.vram_effective_free_mib} MiB) | "
        f"iGPU={hw.igpu_name} | System RAM={hw.system_ram_mib} MiB"
    )
    log_info(f"Hardware Policy: {hw.reason}")

    spec = resolve_model_spec(
        cfg,
        hw,
        cli_model=args.model,
        override_offload=args.gpu_offload,
        override_context=args.context_length,
    )
    log_info(
        f"Target Model Spec: id='{spec.lms_id}' | offload='--gpu {spec.preferred_gpu_offload}' | "
        f"context={spec.preferred_context_length}"
    )

    host = str(srv_cfg.get("host", "127.0.0.1"))
    port = int(srv_cfg.get("port", 1234))

    if args.status:
        verify_local_gguf_exists(cfg, spec)
        reachable = is_server_reachable(host, port)
        log_info(
            f"LM Studio Server (http://{host}:{port}/v1): "
            f"{'ONLINE' if reachable else 'OFFLINE'}"
        )
        if reachable:
            log_info(
                f"Loaded API Models (/v1/models): {get_active_server_models(host, port)}"
            )
        last_state = read_load_state(get_state_file_path(cfg))
        if last_state:
            log_info(f"Last Recorded Load State: {last_state}")
        cp_file = obsidian_config_dir / "community-plugins.json"
        if cp_file.is_file():
            log_info(
                f"Enabled Obsidian Plugins: {cp_file.read_text(encoding='utf-8').strip()}"
            )
        return 0

    explicit_runtime_override = any(
        [
            args.force_reload,
            args.cpu_only,
            args.gpu_offload is not None,
            args.context_length is not None,
        ]
    )

    active_model_id = spec.lms_id
    if not args.skip_server:
        active_model_id = ensure_lmstudio_server_and_model(
            cfg,
            hw,
            spec,
            dry_run=args.dry_run,
            force_reload=explicit_runtime_override,
        )

    system_prompt = read_system_prompt(cfg)
    backup_data = bool(plg_cfg.get("backup_existing_data_json", True))
    prefer_cache = bool(plg_cfg.get("prefer_local_cache", True))
    enabled_plugins: list[str] = []

    # 1. Text Generator (Primary Inline Writing Engine)
    if plg_cfg.get("install_text_generator", True):
        tg_meta = registry.get("text_generator", {})
        tg_id = str(tg_meta.get("plugin_id", "obsidian-textgenerator-plugin"))
        tg_repo = str(
            tg_meta.get("github_repo", "nhaouari/obsidian-textgenerator-plugin")
        )
        tg_assets = [
            str(a)
            for a in tg_meta.get(
                "assets", ["main.js", "manifest.json", "styles.css"]
            )
        ]
        tg_dir = obsidian_config_dir / "plugins" / tg_id

        install_plugin_with_cache(
            tg_repo,
            tg_id,
            tg_dir,
            cache_root,
            tg_assets,
            prefer_local_cache=prefer_cache,
            force_update=args.update_plugins,
            dry_run=args.dry_run,
        )
        tg_payload = build_text_generator_data_json(
            cfg, active_model_id, system_prompt
        )
        write_plugin_data_json(
            obsidian_config_dir,
            tg_id,
            tg_payload,
            backup=backup_data,
            dry_run=args.dry_run,
        )
        enabled_plugins.append(tg_id)

        if plg_cfg.get("seed_writing_templates", True):
            prompts_rel = str(
                plg_cfg.get("prompts_relative_dir", "textgenerator/prompts")
            )
            seed_writing_prompt_templates(
                vault_dir, prompts_rel, dry_run=args.dry_run
            )

    # 2. Smart Composer (Optional Sidebar Chat & Diff Apply Editor)
    if plg_cfg.get("install_smart_composer", False):
        sc_meta = registry.get("smart_composer", {})
        sc_id = str(sc_meta.get("plugin_id", "smart-composer"))
        sc_repo = str(
            sc_meta.get("github_repo", "glowingjade/obsidian-smart-composer")
        )
        sc_assets = [
            str(a)
            for a in sc_meta.get(
                "assets", ["main.js", "manifest.json", "styles.css"]
            )
        ]
        sc_dir = obsidian_config_dir / "plugins" / sc_id

        install_plugin_with_cache(
            sc_repo,
            sc_id,
            sc_dir,
            cache_root,
            sc_assets,
            prefer_local_cache=prefer_cache,
            force_update=args.update_plugins,
            dry_run=args.dry_run,
        )
        write_plugin_data_json(
            obsidian_config_dir,
            sc_id,
            {},
            backup=backup_data,
            custom_merger=lambda existing: merge_smart_composer_data_json(
                existing, cfg, active_model_id, system_prompt
            ),
            dry_run=args.dry_run,
        )
        enabled_plugins.append(sc_id)

    # 3. Copilot (Optional Agent Sidebar)
    if plg_cfg.get("install_copilot", False):
        cp_meta = registry.get("copilot", {})
        cp_id = str(cp_meta.get("plugin_id", "copilot"))
        cp_repo = str(cp_meta.get("github_repo", "logancyang/obsidian-copilot"))
        cp_assets = [
            str(a)
            for a in cp_meta.get(
                "assets", ["main.js", "manifest.json", "styles.css"]
            )
        ]
        cp_dir = obsidian_config_dir / "plugins" / cp_id

        install_plugin_with_cache(
            cp_repo,
            cp_id,
            cp_dir,
            cache_root,
            cp_assets,
            prefer_local_cache=prefer_cache,
            force_update=args.update_plugins,
            dry_run=args.dry_run,
        )
        cp_payload = build_copilot_data_json(
            cfg, active_model_id, system_prompt
        )
        write_plugin_data_json(
            obsidian_config_dir,
            cp_id,
            cp_payload,
            backup=backup_data,
            dry_run=args.dry_run,
        )
        enabled_plugins.append(cp_id)

    if enabled_plugins:
        enable_community_plugins(
            obsidian_config_dir, enabled_plugins, dry_run=args.dry_run
        )

    log_ok(
        f"Obsidian AI writing setup complete (Active Model: '{active_model_id}'). "
        "Reload Obsidian (Ctrl+R) to apply."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
