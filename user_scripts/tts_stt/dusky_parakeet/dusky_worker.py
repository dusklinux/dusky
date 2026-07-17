#!/usr/bin/env python3
# Worker v8.1 BLEEDING EDGE - pip CUDA discovery, D3-cold safe memory cleanup, VRAM unmasking

import sys
import sysconfig
import os
import gc
import time
import traceback
import logging
from pathlib import Path

if sysconfig.get_config_var("Py_GIL_DISABLED") == 1:
    sys.exit(1)

# Prevent early torch/CUDA runtime generation checks on standard imports
os.environ.setdefault("PYTORCH_NVML_BASED_CUDA_CHECK", "1")

logger = logging.getLogger("dusky_worker")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(asctime)s [WORKER] %(message)s'))
logger.addHandler(ch)

def discover_and_set_cuda_paths():
    """Discover CUDA pip libs BEFORE importing onnxruntime to satisfy ORT 1.27+"""
    venv_path = Path(sys.executable).parent.parent
    site_packages = venv_path / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    nvidia_dir = site_packages / "nvidia"
    
    if nvidia_dir.exists():
        cuda_paths = []
        for child in nvidia_dir.iterdir():
            lib_dir = child / "lib"
            if lib_dir.is_dir():
                cuda_paths.append(str(lib_dir))
        if cuda_paths:
            current_ld = os.environ.get("LD_LIBRARY_PATH", "")
            new_ld = ":".join(cuda_paths) + (":" + current_ld if current_ld else "")
            os.environ["LD_LIBRARY_PATH"] = new_ld
            logger.info(f"Discovered and set LD_LIBRARY_PATH for {len(cuda_paths)} nvidia pip packages")

discover_and_set_cuda_paths()

import onnxruntime as rt
if hasattr(rt, "preload_dlls"):
    rt.preload_dlls()

import numpy as np
import soundfile as sf
import onnx_asr

def detect_vram():
    try:
        import subprocess, shutil
        if shutil.which("nvidia-smi"):
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
            if out.returncode == 0:
                return int(out.stdout.strip().splitlines()[0].strip())
    except Exception:
        pass
    return None

class PatchedSession(rt.InferenceSession):
    def __init__(self, path_or_bytes, sess_options=None, providers=None, provider_options=None, **kwargs):
        if sess_options is None:
            sess_options = rt.SessionOptions()
        available = set(rt.get_available_providers())
        vram = detect_vram()
        
        # Enforce 70% clamp for 4GB card profiles to protect desktop compositing headroom
        gpu_limit = int((vram * 0.7 * 1024 * 1024) if vram else 2.5 * 1024 * 1024 * 1024)
        gpu_limit = max(1 * 1024**3, min(gpu_limit, 3 * 1024**3))

        p_names = []
        p_opts = []
        if 'CUDAExecutionProvider' in available:
            p_names.append('CUDAExecutionProvider')
            p_opts.append({'device_id': 0, 'arena_extend_strategy': 'kSameAsRequested', 'gpu_mem_limit': gpu_limit, 'cudnn_conv_algo_search': 'HEURISTIC', 'do_copy_in_default_stream': True})
        elif 'MIGraphXExecutionProvider' in available:
            p_names.append('MIGraphXExecutionProvider')
            p_opts.append({'device_id': 0})
        elif 'ROCmExecutionProvider' in available:
            p_names.append('ROCmExecutionProvider')
            p_opts.append({'device_id': 0, 'arena_extend_strategy': 'kSameAsRequested', 'gpu_mem_limit': gpu_limit})
            
        p_names.append('CPUExecutionProvider')
        p_opts.append({})
        
        sess_options.graph_optimization_level = rt.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.log_severity_level = 3
        
        if any("CUDA" in p or "ROCM" in p or "MIGraphX" in p for p in p_names):
            sess_options.enable_mem_pattern = False
            sess_options.enable_cpu_mem_arena = False
            
        super().__init__(path_or_bytes, sess_options, providers=p_names, provider_options=p_opts, **kwargs)

rt.InferenceSession = PatchedSession

def transcribe_chunk(model, audio: np.ndarray, tmp_dir: Path) -> str:
    try:
        res = model.recognize(audio)
        return (res[0] if isinstance(res, list) else res).strip() if res else ""
    except Exception:
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_dir)) as tf:
                sf.write(tf.name, audio, 16000)
                res = model.recognize(tf.name)
                Path(tf.name).unlink(missing_ok=True)
                return (res[0] if isinstance(res, list) else res).strip() if res else ""
        except Exception as e:
            logger.error(f"chunk fail {e}")
            return ""

def worker_main(task_q, result_q, config: dict):
    # FIXED: Purge the parent daemon's D3-cold mask so this child process can bind the GPU hardware
    if "CUDA_VISIBLE_DEVICES" in os.environ:
        del os.environ["CUDA_VISIBLE_DEVICES"]

    model_name = config.get("model", "nemo-parakeet-tdt-0.6b-v2")
    quant = config.get("quantization", "int8")
    idle_timeout = config.get("idle_timeout", 30)

    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "dusky_stt"
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    logger.info(f"Worker process initialized. Fetching {model_name} (quant={quant})")
    os.environ["HF_XET_HIGH_PERFORMANCE"] = "1"

    model = None
    models_to_try = [
        (model_name, "Config Target"),
        ("nemo-parakeet-tdt-0.6b-v2", "6.05% WER Stable"),
        ("nemo-parakeet-tdt-0.6b-v3", "6.34% WER Multilingual")
    ]
    
    for try_model, info in models_to_try:
        try:
            model = onnx_asr.load_model(try_model, quantization=quant)
            logger.info(f"Successfully attached to model identity: {try_model} ({info})")
            break
        except Exception as e:
            logger.warning(f"Engine identification failure for {try_model}: {e}. Evaluating fallback option...")
            continue

    if model is None:
        result_q.put({"error": "model_load_failed"})
        return

    last_activity = time.time()
    while True:
        try:
            try:
                task = task_q.get(timeout=1.0)
            except Exception:
                if time.time() - last_activity > idle_timeout:
                    logger.info(f"Worker idle threshold reached ({idle_timeout}s). Offloading for D3-cold state.")
                    break
                continue
            
            last_activity = time.time()
            if task.get("type") == "stop":
                break
            if task.get("type") == "audio":
                audio = task["audio"]
                idx = task.get("index", 0)
                start_sec = task.get("start_sec", 0.0)
                text = transcribe_chunk(model, audio, runtime_dir)
                result_q.put({"type": "audio", "index": idx, "start_sec": start_sec, "text": text})
        except Exception as e:
            logger.error(f"Worker hardware loop fault: {e}\n{traceback.format_exc()}")

    try:
        if hasattr(model, "close"):
            model.close()
        del model
    except Exception:
        pass
        
    try:
        import torch
        torch.cuda.empty_cache()
    except ImportError:
        pass
        
    gc.collect()
    logger.info("Context unmapped. Hardware released for suspension.")
